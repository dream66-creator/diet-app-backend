"""阶段 2：Agent 核心执行循环（原生 Function Calling，OpenAI 兼容协议）。

流程：
  1. 读取用户 → needs_onboarding 判断档案缺失 → 组装系统提示词（含动态采集指令）；
  2. 加载近期对话（短期记忆 conversation_messages）拼接消息；
  3. 循环：LLM 返回 tool_calls → dispatch_tool 逐个执行 → 结果以 tool 消息回传；
     直到模型输出最终文本，或达到 MAX_TOOL_ROUNDS 上限；
  4. 全程落库（含工具调用轨迹），支持跨请求续聊与审计。

供应商：默认 DeepSeek（config.LLM_BASE_URL / LLM_MODEL），协议兼容 OpenAI。
"""
import json
from typing import Any, Dict, List, Optional

from flask import current_app
from openai import OpenAI
from sqlalchemy import select

from app.agent.system_prompt import build_system_prompt
from app.agent.tools import dispatch_tool, get_openai_tools
from app.agent.tools.profile_tools import needs_onboarding
from app.extensions import db
from app.models import ConversationMessage, User


def _parse_args(raw: Optional[str]) -> Dict[str, Any]:
    """LLM 返回的 arguments 是 JSON 字符串；解析失败时返回空参（由 pydantic 校验兜底）。"""
    try:
        return json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}


class DietAgent:
    """营养健康管家 Agent 主循环。需在 Flask 应用上下文内使用（工具内部访问 db.session）。"""

    MAX_TOOL_ROUNDS = 6    # 单轮对话最大工具调用轮数，防死循环
    HISTORY_LIMIT = 20     # 载入上下文的近期消息条数（含工具消息）
    REQUEST_TIMEOUT = 60   # LLM 单次请求超时（秒）

    def __init__(self):
        self._client = None

    def _get_client(self) -> OpenAI:
        """懒加载 LLM 客户端：启动时无 Key 也能运行，首次对话时才报错。"""
        if self._client is None:
            cfg = current_app.config
            self._client = OpenAI(api_key=cfg["LLM_API_KEY"] or "missing-key",
                                  base_url=cfg["LLM_BASE_URL"])
        return self._client

    # ---------- 主入口 ----------

    def chat(self, user_id: int, session_id: str, message: str) -> Dict[str, Any]:
        """单轮对话入口。

        返回 {"reply": str, "tool_calls": [轨迹], "session_id": str}；异常时附 "error" 字段。
        """
        user = db.session.get(User, user_id)
        if user is None:
            return {"reply": "用户不存在，请先登录。", "error": "user {} not found".format(user_id),
                    "tool_calls": [], "session_id": session_id}

        system = build_system_prompt(missing_fields=needs_onboarding(user))
        messages = [{"role": "system", "content": system}]
        messages += self._load_history(user_id, session_id)
        messages.append({"role": "user", "content": message})
        self._persist(user_id, session_id, "user", message)

        tool_trace: List[Dict[str, Any]] = []
        if not current_app.config.get("LLM_API_KEY"):
            # 未配置 Key（如别人刚拿到项目）：给出明确指引而非模糊报错，
            # 打卡/日历/扫码等非 AI 功能不受影响
            reply = ("AI 对话功能未配置：请在 backend/.env 中填入 LLM_API_KEY"
                     "（参考 README「快速开始」）。打卡、日历、扫码等功能不受影响。")
            self._persist(user_id, session_id, "assistant", reply)
            return {"reply": reply, "tool_calls": [], "session_id": session_id}
        try:
            for _ in range(self.MAX_TOOL_ROUNDS):
                resp = self._get_client().chat.completions.create(
                    model=current_app.config["LLM_MODEL"],
                    messages=messages,
                    tools=get_openai_tools(),
                    timeout=self.REQUEST_TIMEOUT,
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    reply = msg.content or ""
                    self._persist(user_id, session_id, "assistant", reply)
                    return {"reply": reply, "tool_calls": tool_trace, "session_id": session_id}

                calls = [{"id": tc.id, "name": tc.function.name,
                          "arguments": _parse_args(tc.function.arguments)}
                         for tc in msg.tool_calls]
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [{
                        "id": c["id"], "type": "function",
                        "function": {"name": c["name"],
                                     "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                    } for c in calls],
                })
                self._persist(user_id, session_id, "assistant", msg.content or "", tool_calls=calls)

                for c in calls:
                    result = dispatch_tool(c["name"], c["arguments"])
                    tool_trace.append({"name": c["name"], "arguments": c["arguments"], "result": result})
                    content = json.dumps(result, ensure_ascii=False)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
                    self._persist(user_id, session_id, "tool", content, tool_call_id=c["id"])

            # 超出轮数上限：兜底收尾
            reply = "抱歉，这轮处理步骤有点多，请把需求说得简单一些，我们一步步来。"
            self._persist(user_id, session_id, "assistant", reply)
            return {"reply": reply, "tool_calls": tool_trace, "session_id": session_id}
        except Exception as exc:
            reply = "抱歉，AI 服务暂时不可用，请稍后再试。（{}）".format(exc)
            return {"reply": reply, "error": str(exc), "tool_calls": tool_trace, "session_id": session_id}

    # ---------- 短期记忆 ----------

    def _load_history(self, user_id: int, session_id: str,
                      limit: int = HISTORY_LIMIT) -> List[Dict[str, Any]]:
        """读取最近 limit 条对话消息，还原为 OpenAI messages 格式（含工具调用链）。"""
        rows = db.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id,
                   ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.id.desc())
            .limit(limit)
        ).scalars().all()

        history: List[Dict[str, Any]] = []
        for m in reversed(rows):
            if m.role == "user":
                history.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                entry: Dict[str, Any] = {"role": "assistant", "content": m.content or ""}
                if m.tool_calls:
                    entry["tool_calls"] = [{
                        "id": tc["id"], "type": "function",
                        "function": {"name": tc["name"],
                                     "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                    } for tc in m.tool_calls]
                history.append(entry)
            elif m.role == "tool" and m.tool_call_id:
                history.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        return history

    def _persist(self, user_id: int, session_id: str, role: str, content: str,
                 tool_calls: Optional[List[Dict]] = None,
                 tool_call_id: Optional[str] = None) -> None:
        """落库一条对话消息（每条独立提交，简化事务边界）。"""
        db.session.add(ConversationMessage(
            user_id=user_id, session_id=session_id, role=role, content=content,
            tool_calls=tool_calls, tool_call_id=tool_call_id,
        ))
        db.session.commit()
