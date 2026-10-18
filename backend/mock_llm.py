# -*- coding: utf-8 -*-
"""临时 Mock LLM 服务器：模拟 DeepSeek 的 OpenAI 兼容 Function Calling 行为。

用途：在无真实 Key 的情况下，确定性验证 Agent 主循环（core.DietAgent）的完整链路：
工具选择 → 参数生成 → 结果回传 → 基于结果生成最终回复。
协议与真实 DeepSeek 完全一致（POST /chat/completions + tools + tool role 消息）。
"""
import json
import re

from flask import Flask, jsonify, request

app = Flask(__name__)


def _tool_call(name, args, cid):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": cid,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


def _final(content):
    return {"role": "assistant", "content": content}


def _mock_plan_from_prompt(prompt):
    """从 generate_meal_plan 的提示词中解析食物 id，生成一份 JSON 方案（模拟 LLM 输出）。"""
    rows = re.findall(r"id=(\d+) (\S+)（每100g:[^）]*分类:(\w+)）", prompt)
    by_cat = {}
    for fid, name, cat in rows:
        by_cat.setdefault(cat, []).append((int(fid), name))

    def pick(cat, idx=0):
        pool = by_cat.get(cat, [])
        return pool[idx] if idx < len(pool) else (pool[0] if pool else None)

    def item(spec, grams):
        fid, name = spec
        return {"food_id": fid, "food_name": name, "amount_g": grams}

    meals = {"breakfast": [], "lunch": [], "dinner": [], "snack": []}
    for spec, grams in [(pick("staple"), 200), (pick("meat"), 150), (pick("dairy"), 250)]:
        if spec:
            meals["breakfast"].append(item(spec, grams))
    for spec, grams in [(pick("staple"), 200), (pick("meat", 1) or pick("meat"), 150), (pick("vegetable"), 150)]:
        if spec:
            meals["lunch"].append(item(spec, grams))
    for spec, grams in [(pick("staple", 1) or pick("staple"), 150), (pick("fish"), 150), (pick("vegetable", 1) or pick("vegetable"), 150)]:
        if spec:
            meals["dinner"].append(item(spec, grams))
    return {"plans": [{"meals": meals, "note": "mock 智能搭配方案"}]}


@app.post("/chat/completions")
def completions():
    body = request.get_json(force=True)
    messages = body.get("messages", [])

    # 只统计「本轮对话」（最后一条用户消息之后）的工具调用与结果，
    # 与真实 LLM 行为一致：历史消息仅作上下文，不影响本轮决策状态
    last_user_idx = max(i for i, m in enumerate(messages) if m.get("role") == "user")
    turn = messages[last_user_idx + 1:]
    last_user = messages[last_user_idx]["content"]
    tool_names = []
    for m in turn:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tool_names.append(tc["function"]["name"])
    last_tool = tool_names[-1] if tool_names else None
    tool_results = [json.loads(m["content"]) for m in turn if m.get("role") == "tool"]

    # 1) generate_meal_plan 工具内部的 LLM 调用（纯提示词，要求 JSON 输出）
    if "你是注册营养师" in last_user and "输出严格的 JSON" in last_user:
        return jsonify({
            "id": "chatcmpl-mock-plan", "object": "chat.completion", "model": "deepseek-chat",
            "choices": [{"index": 0, "message": _final(json.dumps(_mock_plan_from_prompt(last_user), ensure_ascii=False)),
                         "finish_reason": "stop"}],
        })

    # 2) 异常场景：调用不存在的工具
    if "魔法" in last_user:
        if last_tool is None:
            msg = _tool_call("nonexistent_tool", {}, "call_x")
        else:
            err = tool_results[-1].get("error", "工具不可用")
            msg = _final("抱歉，{}。请换个说法试试～".format(err))
        return _ok(msg)

    # 3) 打卡：查食物库 → 落库 → 基于结果总结
    if "我吃了" in last_user or "打卡" in last_user:
        if last_tool is None:
            msg = _tool_call("search_food_database", {"query": "宫保鸡丁"}, "call_1")
        elif last_tool == "search_food_database":
            msg = _tool_call("record_diet_to_db", {
                "user_id": 1, "meal_type": "lunch", "source": "nlp",
                "items": [{"food_name": "宫保鸡丁", "amount_g": 300},
                          {"food_name": "米饭", "amount_g": 200}]}, "call_2")
        else:
            r = tool_results[-1]
            total = r.get("total", {}).get("calories_kcal", 0)
            remaining = r.get("remaining", {}).get("calories_kcal")
            msg = _final("已为你记录午餐 ✅ 本餐共 **{} kcal**，今日还可吃 {} kcal。".format(
                total, remaining if remaining is not None else "——"))
        return _ok(msg)

    # 4) 热量目标：读档案 →（缺失则采集）→ 回显目标
    if "每天该吃多少" in last_user or "多少热量" in last_user:
        if last_tool is None:
            msg = _tool_call("get_user_profile", {"user_id": 1}, "call_1")
        elif last_tool == "get_user_profile":
            profile = tool_results[-1].get("profile", {})
            if profile.get("targets", {}).get("calories") is None:
                msg = _tool_call("update_user_profile", {
                    "user_id": 1, "gender": "male", "age": 25,
                    "height_cm": 170, "weight_kg": 65,
                    "activity_level": "light", "goal": "maintain"}, "call_2")
            else:
                t = profile["targets"]
                msg = _final("你的每日目标：热量 **{} kcal**，蛋白质 {}g、碳水 {}g、脂肪 {}g。".format(
                    t["calories"], t["protein_g"], t["carbs_g"], t["fat_g"]))
        else:
            t = tool_results[-1].get("profile", {}).get("targets", {})
            msg = _final("档案已更新 ✅ 你的每日目标：热量 **{} kcal**，蛋白质 {}g、碳水 {}g、脂肪 {}g。".format(
                t.get("calories"), t.get("protein_g"), t.get("carbs_g"), t.get("fat_g")))
        return _ok(msg)

    # 5) 食谱规划：生成 → 回显日期
    if "规划" in last_user or "食谱" in last_user or "配个餐" in last_user:
        if last_tool is None:
            msg = _tool_call("generate_meal_plan", {"user_id": 1, "days": 1}, "call_1")
        else:
            r = tool_results[-1]
            plans = r.get("plans") or []
            d = plans[0]["plan_date"] if plans else "?"
            msg = _final("已为你生成 {} 的减脂餐方案 ✨ 共 {} kcal，可在日历页查看和调整。".format(
                d, plans[0].get("total_nutrition", {}).get("calories_kcal", "?") if plans else "?"))
        return _ok(msg)

    # 6) 饮水
    if "水" in last_user:
        if last_tool is None:
            msg = _tool_call("add_water_intake", {"user_id": 1, "volume_ml": 250}, "call_1")
        else:
            r = tool_results[-1]
            msg = _final("已记录饮水 💧 今日累计 **{} ml**，目标 {} ml，还差 {} ml。".format(
                r.get("today_total_ml"), r.get("goal_ml"), r.get("remaining_ml")))
        return _ok(msg)

    # 7) 兜底
    return _ok(_final("你好，我是营养管家小管，告诉我你吃了什么吧～"))


def _ok(msg):
    return jsonify({
        "id": "chatcmpl-mock", "object": "chat.completion", "model": "deepseek-chat",
        "choices": [{"index": 0, "message": msg,
                     "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9999, debug=False)
