"""附加工具：Agent 上下文读取 —— 用户档案（长期记忆）与今日饮食汇总。

这两个工具是主循环的「眼睛」：回答任何个性化问题（还剩多少热量、
我忌口什么、我的目标）之前，Agent 应先调用它们获取事实，而不是凭空猜测。
"""
from datetime import date, datetime, time

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent.tools.base import fail, ok, register_tool
from app.extensions import db
from app.models import DietRecord, User, WaterRecord


class UserIdInput(BaseModel):
    user_id: int = Field(description="用户 ID")


def get_user_profile(user_id: int) -> dict:
    """读取用户档案（长期记忆）：生理数据、目标、忌口、预算、已缓存的目标营养素。"""
    user = db.session.get(User, user_id)
    if user is None:
        return fail("用户 {} 不存在".format(user_id))
    return ok(profile=user.to_dict())


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min)


def today_summary(user_id: int) -> dict:
    """今日已摄入汇总（饮食 + 饮水）。record_tools 与 get_today_diet_summary 共用。"""
    start = _day_start(date.today())
    diet = db.session.execute(
        select(
            func.coalesce(func.sum(DietRecord.calories_kcal), 0),
            func.coalesce(func.sum(DietRecord.protein_g), 0),
            func.coalesce(func.sum(DietRecord.fat_g), 0),
            func.coalesce(func.sum(DietRecord.carbs_g), 0),
        ).where(DietRecord.user_id == user_id, DietRecord.eaten_at >= start)
    ).one()
    water = db.session.execute(
        select(func.coalesce(func.sum(WaterRecord.volume_ml), 0))
        .where(WaterRecord.user_id == user_id, WaterRecord.drank_at >= start)
    ).scalar_one()
    return {
        "calories_kcal": round(float(diet[0]), 1),
        "protein_g": round(float(diet[1]), 1),
        "fat_g": round(float(diet[2]), 1),
        "carbs_g": round(float(diet[3]), 1),
        "water_ml": int(water),
    }


def get_today_diet_summary(user_id: int) -> dict:
    """今日饮食与饮水汇总，含目标对比与剩余额度。

    回答「我今天吃了多少」「还能再吃什么」「今天喝水够了吗」之前必须调用。
    """
    user = db.session.get(User, user_id)
    if user is None:
        return fail("用户 {} 不存在".format(user_id))

    eaten = today_summary(user_id)

    def diff(target, value):
        return None if target is None else round(target - value, 1)

    remaining = {
        "calories_kcal": diff(user.target_calories, eaten["calories_kcal"]),
        "protein_g": diff(user.target_protein_g, eaten["protein_g"]),
        "fat_g": diff(user.target_fat_g, eaten["fat_g"]),
        "carbs_g": diff(user.target_carbs_g, eaten["carbs_g"]),
        "water_ml": max(0, user.water_goal_ml - eaten["water_ml"]),
    }
    return ok(
        date=str(date.today()),
        eaten=eaten,
        targets={
            "calories_kcal": user.target_calories,
            "protein_g": user.target_protein_g,
            "fat_g": user.target_fat_g,
            "carbs_g": user.target_carbs_g,
            "water_ml": user.water_goal_ml,
        },
        remaining=remaining,
    )


register_tool(
    "get_user_profile",
    "读取用户档案（长期记忆）：性别/年龄/身高/体重、目标（减脂/增肌/维持/控糖）、"
    "忌口与过敏原、口味偏好、每日预算、已缓存的目标营养素。"
    "回答与用户个人情况相关的问题、生成个性化食谱之前，应先调用本工具。",
    UserIdInput,
    get_user_profile,
)
register_tool(
    "get_today_diet_summary",
    "查询用户今日已摄入的热量、蛋白质、脂肪、碳水与饮水汇总，以及距离目标的剩余额度。"
    "回答「我今天吃了多少」「还能吃什么」「喝水够了吗」之前必须调用本工具。",
    UserIdInput,
    get_today_diet_summary,
)
