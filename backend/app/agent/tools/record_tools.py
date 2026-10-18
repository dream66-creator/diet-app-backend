"""工具 6~7：饮食打卡落库、饮水记录（已完整实现，阶段 3 联调）。"""
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.tools.base import fail, ok, register_tool
from app.agent.tools.context_tools import today_summary
from app.extensions import db
from app.models import DietRecord, Food, User, WaterRecord

MEAL_TYPE = Literal["breakfast", "lunch", "dinner", "snack"]
SOURCE = Literal["nlp", "manual", "barcode", "photo", "favorite"]

# 食物库每 100g 字段 → 打卡记录快照字段
_PER100_FIELDS = {
    "calories_kcal": "calories_kcal_per_100g",
    "protein_g": "protein_g_per_100g",
    "fat_g": "fat_g_per_100g",
    "carbs_g": "carbs_g_per_100g",
    "fiber_g": "fiber_g_per_100g",
    "sodium_mg": "sodium_mg_per_100g",
}


class DietItemInput(BaseModel):
    """单条餐食拆解结果（Agent 从自然语言或图片识别中产出）。"""
    food_id: Optional[int] = Field(default=None, description="食物库 ID（已知时提供）")
    food_name: str = Field(description="食物名称（落库快照）")
    amount_g: float = Field(gt=0, le=5000, description="食用克数")
    meal_type: Optional[MEAL_TYPE] = Field(
        default=None, description="餐次；缺省继承外层 meal_type")
    # 可选覆盖值：拍照识餐时由多模态模型给出估算，优先于食物库换算
    calories_kcal: Optional[float] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sodium_mg: Optional[float] = None


class RecordDietInput(BaseModel):
    user_id: int = Field(description="用户 ID")
    meal_type: MEAL_TYPE = Field(description="默认餐次（breakfast/lunch/dinner/snack）")
    items: List[DietItemInput] = Field(min_length=1, description="本次餐食的拆解条目")
    source: SOURCE = Field(default="nlp", description="数据来源：nlp/manual/barcode/photo/favorite")
    eaten_at: Optional[datetime] = Field(default=None, description="食用时间，缺省为当前时间")


def record_diet_to_db(user_id: int, meal_type: str, items: List[Dict],
                      source: str = "nlp", eaten_at: Optional[datetime] = None) -> dict:
    """餐食打卡落库：逐条换算营养快照并写入 diet_records。

    换算优先级：条目覆盖值（拍照估算）> 食物库（food_id 或名称匹配）按克数缩放 > 0 值兜底。
    营养以快照随记录保存：食物库日后修订不会改变历史打卡。
    """
    user = db.session.get(User, user_id)
    if user is None:
        return fail("用户 {} 不存在".format(user_id))

    records, per_item = [], []
    for raw in items:
        item = DietItemInput.model_validate(raw)
        nutrition = _resolve_nutrition(item)
        record = DietRecord(
            user_id=user_id,
            meal_type=item.meal_type or meal_type,
            food_id=item.food_id,
            food_name=item.food_name,
            amount_g=item.amount_g,
            source=source,
            eaten_at=eaten_at or datetime.now(),
            **nutrition,
        )
        db.session.add(record)
        records.append(record)
        per_item.append({"food_name": item.food_name, "amount_g": item.amount_g,
                         "meal_type": item.meal_type or meal_type, **nutrition})

    db.session.commit()

    total = {
        "calories_kcal": round(sum(r.calories_kcal for r in records), 1),
        "protein_g": round(sum(r.protein_g for r in records), 1),
        "fat_g": round(sum(r.fat_g for r in records), 1),
        "carbs_g": round(sum(r.carbs_g for r in records), 1),
    }
    eaten = today_summary(user_id)
    return ok(
        record_ids=[r.id for r in records],
        per_item=per_item,
        total=total,
        today_total=eaten,
        remaining=_remaining(user, eaten),
        summary="已记录 {} 条餐食，共 {} kcal".format(len(records), total["calories_kcal"]),
    )


def find_food(food_id=None, food_name=None):
    """按 ID 或名称匹配食物库（打卡换算 / 收藏 / 方案重算共用）。"""
    food = None
    if food_id:
        food = db.session.get(Food, food_id)
    if food is None and food_name:
        food = db.session.execute(
            select(Food).where(Food.name.ilike(food_name)).limit(1)
        ).scalar_one_or_none()
    return food


def _resolve_nutrition(item: DietItemInput) -> dict:
    """按优先级解析单条餐食的营养快照。"""
    food = find_food(item.food_id, item.food_name)
    if food is not None:
        scale = item.amount_g / 100.0
        return {k: round(getattr(food, attr) * scale, 1) for k, attr in _PER100_FIELDS.items()}
    # 食物库未命中：使用覆盖值（拍照估算场景）兜底，无覆盖值记 0
    return {
        "calories_kcal": item.calories_kcal or 0.0,
        "protein_g": item.protein_g or 0.0,
        "fat_g": item.fat_g or 0.0,
        "carbs_g": item.carbs_g or 0.0,
        "fiber_g": item.fiber_g or 0.0,
        "sodium_mg": item.sodium_mg,
    }


def _remaining(user: User, eaten: dict) -> dict:
    def diff(target, value):
        return None if target is None else round(target - value, 1)

    return {
        "calories_kcal": diff(user.target_calories, eaten["calories_kcal"]),
        "protein_g": diff(user.target_protein_g, eaten["protein_g"]),
        "fat_g": diff(user.target_fat_g, eaten["fat_g"]),
        "carbs_g": diff(user.target_carbs_g, eaten["carbs_g"]),
        "water_ml": max(0, user.water_goal_ml - eaten["water_ml"]),
    }


class WaterInput(BaseModel):
    user_id: int = Field(description="用户 ID")
    volume_ml: int = Field(gt=0, le=2000, description="本次饮水量（毫升）；「一杯水」约 250ml，「一瓶水」约 500ml")


def add_water_intake(user_id: int, volume_ml: int) -> dict:
    """记录一次饮水，返回今日累计、目标与剩余。"""
    user = db.session.get(User, user_id)
    if user is None:
        return fail("用户 {} 不存在".format(user_id))
    db.session.add(WaterRecord(user_id=user_id, volume_ml=volume_ml, source="agent"))
    db.session.commit()

    today_ml = today_summary(user_id)["water_ml"]
    goal = user.water_goal_ml
    return ok(
        added_ml=volume_ml,
        today_total_ml=today_ml,
        goal_ml=goal,
        remaining_ml=max(0, goal - today_ml),
        progress_pct=round(min(100.0, today_ml / goal * 100), 1),
    )


register_tool(
    "record_diet_to_db",
    "将餐食拆解结果写入打卡记录表（diet_records）。用户说「我吃了XX」「打卡XX」时调用；"
    "先拆解食物与重量（必要时用 search_food_database 换算营养），再一次性写入本工具。"
    "写入后返回本餐合计、今日累计与剩余额度，用于向用户反馈。",
    RecordDietInput,
    record_diet_to_db,
)
register_tool(
    "add_water_intake",
    "记录一次饮水（water_records 表）。用户说「喝了XX水」「提醒我喝水并记录」时调用；"
    "返回今日累计与目标对比，用于向用户反馈。",
    WaterInput,
    add_water_intake,
)
