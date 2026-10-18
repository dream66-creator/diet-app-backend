"""Agent 工具注册表基础设施（阶段 1）。

统一采用 OpenAI Function Calling 协议暴露工具，每个工具由三部分组成：
  1) name / description —— LLM 判断何时调用、如何组织参数；
  2) JSON Schema（由 Pydantic 参数模型自动生成）—— 参数类型与约束；
  3) 可执行函数 —— 实际执行，统一返回 {"ok": True, ...} / {"ok": False, "error": "..."}。

阶段 2 的 Agent 主循环通过 get_openai_tools() 取 schema、dispatch_tool() 分发执行；
工具内部使用 db.session（Flask 应用上下文内运行，由阶段 3 的路由保证）。
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, Type

from pydantic import BaseModel


@dataclass
class ToolDef:
    name: str
    description: str
    param_model: Type[BaseModel]
    func: Callable[..., Dict[str, Any]]


TOOL_REGISTRY: Dict[str, ToolDef] = {}


def register_tool(name: str, description: str, param_model: Type[BaseModel],
                  func: Callable[..., Dict[str, Any]]) -> None:
    """注册工具：工具模块在底部调用本函数完成注册。"""
    TOOL_REGISTRY[name] = ToolDef(name=name, description=description,
                                  param_model=param_model, func=func)


def get_openai_tools():
    """返回 OpenAI Chat Completions `tools` 参数格式的完整工具列表。

    兼容性处理（DeepSeek 等对 JSON Schema 支持较保守的供应商）：
    - 嵌套模型（如打卡工具的 items 列表）会生成 $defs/$ref，这里内联展开；
    - 移除 title 等非必需字段。
    """
    tools = []
    for t in TOOL_REGISTRY.values():
        schema = t.param_model.model_json_schema()
        defs = schema.pop("$defs", {})
        schema = _resolve_refs(schema, defs)
        schema = _strip_keys(schema, ("title",))
        tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": schema,
            },
        })
    return tools


def _resolve_refs(node, defs):
    """递归把 $ref 替换为 $defs 中的实际定义（内联化）。"""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _resolve_refs(defs[ref.rsplit("/", 1)[-1]], defs)
        return {k: _resolve_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node


def _strip_keys(node, keys):
    """递归移除 schema 中不需要的键。"""
    if isinstance(node, dict):
        return {k: _strip_keys(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_strip_keys(item, keys) for item in node]
    return node


def dispatch_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """按名称校验参数并执行工具（阶段 2 主循环调用）。

    任何异常（参数非法 / 工具未实现 / 运行时错误）都转成可读的失败响应，
    让 LLM 能据此向用户解释或调整调用，而不是让整个对话崩溃。
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return fail("未知工具: {}".format(name))
    try:
        args = tool.param_model.model_validate(arguments)
        return tool.func(**args.model_dump())
    except Exception as exc:
        return fail("工具 {} 执行失败: {}".format(name, exc))


def ok(**data) -> Dict[str, Any]:
    """成功响应封装：{"ok": True, <业务数据>}。"""
    return {"ok": True, **data}


def fail(message: str) -> Dict[str, Any]:
    """失败响应封装：{"ok": False, "error": "..."}。"""
    return {"ok": False, "error": message}
