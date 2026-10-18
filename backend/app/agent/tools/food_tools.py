"""工具 1~3：食物库查询 / 扫码查询 / 拍照识餐。

设计要点：analyze_food_image 只返回识别候选、绝不落库 ——
识别结果由 Agent 评估并与用户确认后，再调用 record_diet_to_db 落库
（识别结果不直接强加给用户）。
"""
import base64
import json
import os
import re
from typing import Optional

from flask import current_app
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, or_, select

from app.agent.tools.base import fail, ok, register_tool
from app.extensions import db
from app.models import Food

VISION_SYSTEM_PROMPT = (
    "你是营养分析助手。识别用户照片中的食物，估算每种食物的重量（克）与热量及营养素。"
    "只输出 JSON，格式："
    '{"items": [{"food_name": "宫保鸡丁", "amount_g": 200, "confidence": 0.9, '
    '"calories_kcal": 380, "protein_g": 28, "fat_g": 24, "carbs_g": 12}], '
    '"note": "对整餐的简短说明"}'
)

PER_100G_FIELDS = (
    "calories_kcal_per_100g", "protein_g_per_100g", "fat_g_per_100g",
    "carbs_g_per_100g", "fiber_g_per_100g", "sodium_mg_per_100g",
)


def food_to_dict(food: Food) -> dict:
    """Food ORM 对象 → Agent 可读的标准视图。"""
    return {
        "food_id": food.id,
        "name": food.name,
        "aliases": food.aliases,
        "category": food.category,
        "unit": food.unit,
        "default_portion_g": food.default_portion_g,
        "brand": food.brand,
        "nutrition_per_100g": {
            "calories_kcal": food.calories_kcal_per_100g,
            "protein_g": food.protein_g_per_100g,
            "fat_g": food.fat_g_per_100g,
            "carbs_g": food.carbs_g_per_100g,
            "fiber_g": food.fiber_g_per_100g,
            "sodium_mg": food.sodium_mg_per_100g,
        },
    }


# ---------- 工具 1：食物库查询 ----------

class SearchFoodInput(BaseModel):
    query: str = Field(description="食物名称关键词，如「宫保鸡丁」「米饭」「牛奶」")
    limit: int = Field(default=5, ge=1, le=20, description="返回候选数量上限")


def search_food_database(query: str, limit: int = 5) -> dict:
    """在内置食物库中按名称/别名模糊搜索，返回每 100g 营养信息。"""
    q = "%{}%".format(query)
    foods = db.session.execute(
        select(Food)
        .where(or_(Food.name.ilike(q), cast(Food.aliases, String).ilike(q)))
        .order_by(Food.name)
        .limit(limit)
    ).scalars().all()
    if not foods:
        return ok(results=[], count=0,
                  hint="食物库中未找到「{}」，可尝试更宽泛的关键词".format(query))
    return ok(count=len(foods), results=[food_to_dict(f) for f in foods])


# ---------- 工具 2：扫码查询 ----------

class BarcodeInput(BaseModel):
    barcode: str = Field(description="商品条形码（EAN-8 / EAN-13 等纯数字串）")


def fetch_barcode_info(barcode: str) -> dict:
    """扫码查询包装食品：优先本地食物库，未命中时调用外部条码 API。"""
    food = db.session.execute(
        select(Food).where(Food.barcode == barcode)
    ).scalar_one_or_none()
    if food is not None:
        return ok(source="local", food=food_to_dict(food))

    external = _query_external_barcode_api(barcode)
    if external is not None:
        return ok(source="external", food=external)

    return ok(found=False, barcode=barcode,
              hint="本地库与外部 API 均未查到该条码，可请用户手动描述该食品")


def _query_external_barcode_api(barcode: str):
    """调用外部条码 API（中国物品编码中心 / Open Food Facts 等）。

    TODO(阶段 3)：实现 HTTP 查询、结果标准化为 food_to_dict 结构、
    并可选回填本地食物库。阶段 1 仅预留接口。
    """
    url = current_app.config.get("BARCODE_API_URL", "")
    if not url:
        return None
    # TODO(阶段 3): requests.get(url, params={"barcode": barcode}, timeout=5) → 标准化
    return None


def _resolve_image_source(image_url: str) -> str:
    """把本站 /uploads/ 相对地址转换为 data URL。

    外部视觉 API（智谱/OpenAI 等云端服务）无法访问本机 localhost，
    必须把图片内容直接编码进请求；外部公网 URL 则原样透传。
    """
    prefix = "/uploads/"
    if not image_url.startswith(prefix):
        return image_url
    filename = os.path.basename(image_url[len(prefix):])  # basename 防目录穿越
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if not os.path.isfile(path):
        return image_url
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(ext, "jpeg")
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return "data:image/{};base64,{}".format(mime, b64)


def _extract_json(text: str):
    """从模型自由文本输出中容错提取 JSON（剥离 markdown 代码块，截取首尾大括号）。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    try:
        return json.loads(text)
    except ValueError:
        return None


# ---------- 工具 3：拍照识餐 ----------

class AnalyzeImageInput(BaseModel):
    image_url: str = Field(description="待识别照片的 URL 或 data URL（前端拍照后上传所得）")
    user_goal: Optional[str] = Field(
        default=None, description="用户当前目标（lose_fat/gain_muscle/maintain/control_sugar），估算参考")


def analyze_food_image(image_url: str, user_goal: Optional[str] = None) -> dict:
    """调用多模态大模型识别照片中的食物并估算重量与热量。

    只返回候选结果，不写入任何表 —— 由 Agent 评估并向用户确认后，
    再调用 record_diet_to_db 落库（识别结果不直接强加给用户）。

    返回结构：
    {"ok": True,
     "items": [{"food_name": "宫保鸡丁", "amount_g": 200, "confidence": 0.85,
                "calories_kcal": 380, "protein_g": 28.0, "fat_g": 24.0,
                "carbs_g": 12.0}, ...],
     "note": "整体估算说明（餐盘构成、估算依据、不确定性）"}
    """
    cfg = current_app.config
    if not cfg.get("LLM_VISION_MODEL"):
        return fail("视觉识别服务未配置：请在 .env 中设置 LLM_VISION_MODEL（DeepSeek 官方暂无视觉模型，需另配供应商）")

    # 外部视觉 API 无法访问本机 /uploads/ 地址，先转成 data URL
    source = _resolve_image_source(image_url)
    vision_key = cfg.get("LLM_VISION_API_KEY") or cfg["LLM_API_KEY"] or "missing-key"
    client = OpenAI(api_key=vision_key,
                    base_url=cfg.get("LLM_VISION_BASE_URL") or cfg["LLM_BASE_URL"])
    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "请识别这张照片中的食物"},
            {"type": "image_url", "image_url": {"url": source}},
        ]},
    ]
    # 优先请求 JSON 输出；部分视觉供应商不支持 response_format 时降级为自由文本再容错解析
    data = None
    try:
        resp = client.chat.completions.create(
            model=cfg["LLM_VISION_MODEL"], messages=messages,
            response_format={"type": "json_object"}, timeout=90)
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        try:
            resp = client.chat.completions.create(
                model=cfg["LLM_VISION_MODEL"], messages=messages, timeout=90)
            data = _extract_json(resp.choices[0].message.content or "")
        except Exception as exc:
            return fail("照片识别失败：{}".format(exc))
    if not isinstance(data, dict):
        return fail("照片识别结果解析失败，请重试或手动描述食物")

    items = []
    for it in (data.get("items") or [])[:10]:
        try:
            items.append({
                "food_name": str(it.get("food_name") or "未知食物"),
                "amount_g": round(float(it.get("amount_g") or 0), 1),
                "confidence": round(float(it.get("confidence") or 0), 2),
                "calories_kcal": round(float(it.get("calories_kcal") or 0), 1),
                "protein_g": round(float(it.get("protein_g") or 0), 1),
                "fat_g": round(float(it.get("fat_g") or 0), 1),
                "carbs_g": round(float(it.get("carbs_g") or 0), 1),
            })
        except (TypeError, ValueError):
            continue
    if not items:
        return fail("照片中未能识别出食物，请换一张更清晰的照片或手动描述")
    return ok(items=items, note=str(data.get("note") or ""),
              hint="以上为模型估算结果，请先与用户确认再打卡")


register_tool(
    "search_food_database",
    "在内置食物库中按名称/别名模糊搜索，返回每 100g 的热量、蛋白质、脂肪、碳水、膳食纤维、钠。"
    "用户提到具体食物名称（如「宫保鸡丁多少热量」）或打卡需要换算营养时调用。",
    SearchFoodInput,
    search_food_database,
)
register_tool(
    "fetch_barcode_info",
    "扫码查询包装食品营养表：按条形码查本地食物库，未命中时查外部条码 API。"
    "用户提供条形码数字或说「扫到了这个码」时调用。",
    BarcodeInput,
    fetch_barcode_info,
)
register_tool(
    "analyze_food_image",
    "调用多模态大模型识别照片中的食物并估算重量与热量。只返回候选结果、不落库；"
    "必须先将识别结果告知用户并获得确认，再调用 record_diet_to_db 记录。"
    "用户发送食物照片或要求「识别这张图」时调用。",
    AnalyzeImageInput,
    analyze_food_image,
)
