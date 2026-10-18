"""工具 4：BMR / TDEE / 宏量营养素目标计算（Mifflin-St Jeor 公式）。

纯函数、不落库 —— 计算的是「理论目标」；
是否把结果缓存回 users 表（target_* 字段）由阶段 2 的主循环按场景决定。
"""
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.tools.base import fail, ok, register_tool

ACTIVITY_FACTORS = {
    "sedentary": 1.2,     # 久坐：几乎不运动
    "light": 1.375,       # 轻度：每周 1-3 次运动
    "moderate": 1.55,     # 中度：每周 3-5 次运动
    "active": 1.725,      # 高度：每周 6-7 次运动
    "very_active": 1.9,   # 极高：体力劳动 + 每日训练
}

GOAL_CALORIE_ADJUST = {
    "lose_fat": -400,     # 减脂：约 400 kcal 赤字
    "gain_muscle": 300,   # 增肌：约 300 kcal 盈余
    "maintain": 0,
    "control_sugar": -100,  # 控糖：轻微赤字 + 低碳水分配
}

MACRO_SPLIT = {  # (蛋白, 碳水, 脂肪) 供能占比
    "lose_fat": (0.30, 0.40, 0.30),
    "gain_muscle": (0.30, 0.45, 0.25),
    "maintain": (0.20, 0.50, 0.30),
    "control_sugar": (0.25, 0.40, 0.35),
}

KCAL_PER_GRAM = {"protein": 4, "carbs": 4, "fat": 9}


class BMRInput(BaseModel):
    """入参即用户生理数据；Agent 先调用 get_user_profile 取数后填入。"""
    gender: Literal["male", "female"] = Field(description="性别：male 男 / female 女")
    age: int = Field(ge=10, le=100, description="年龄（岁）")
    height_cm: float = Field(gt=0, le=250, description="身高（厘米）")
    weight_kg: float = Field(gt=0, le=350, description="体重（公斤）")
    activity_level: Literal["sedentary", "light", "moderate", "active", "very_active"] = Field(
        default="light", description="活动量等级")
    goal: Literal["lose_fat", "gain_muscle", "maintain", "control_sugar"] = Field(
        default="maintain", description="用户目标")


def calculate_bmr_tdee(gender: str, age: int, height_cm: float, weight_kg: float,
                       activity_level: str = "light", goal: str = "maintain") -> dict:
    """计算 BMR（Mifflin-St Jeor）、TDEE、目标热量与宏量营养素分配。

    返回结构：
    {"ok": True,
     "bmr": 1450.0, "tdee": 2000.0, "target_calories": 1600,
     "macros": {"protein_g": 120.0, "carbs_g": 160.0, "fat_g": 53.3},
     "meal_calorie_split": {"breakfast": 0.3, "lunch": 0.4, "dinner": 0.3},
     "formula": "Mifflin-St Jeor", "note": "…"}
    """
    if activity_level not in ACTIVITY_FACTORS or goal not in MACRO_SPLIT:
        return fail("非法的活动量或目标取值: activity_level={}, goal={}".format(activity_level, goal))

    # Mifflin-St Jeor 公式
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if gender == "male" else -161)
    tdee = bmr * ACTIVITY_FACTORS[activity_level]
    target_calories = max(1200, tdee + GOAL_CALORIE_ADJUST[goal])  # 安全下限 1200 kcal

    p_ratio, c_ratio, f_ratio = MACRO_SPLIT[goal]
    macros = {
        "protein_g": round(target_calories * p_ratio / KCAL_PER_GRAM["protein"], 1),
        "carbs_g": round(target_calories * c_ratio / KCAL_PER_GRAM["carbs"], 1),
        "fat_g": round(target_calories * f_ratio / KCAL_PER_GRAM["fat"], 1),
    }

    return ok(
        formula="Mifflin-St Jeor",
        bmr=round(bmr, 1),
        tdee=round(tdee, 1),
        target_calories=round(target_calories),
        macros=macros,
        meal_calorie_split={"breakfast": 0.3, "lunch": 0.4, "dinner": 0.3},
        note="目标「{}」，热量调整 {:+.0f} kcal".format(goal, GOAL_CALORIE_ADJUST[goal]),
    )


register_tool(
    "calculate_bmr_tdee",
    "按 Mifflin-St Jeor 公式计算用户的基础代谢(BMR)、每日总消耗(TDEE)、"
    "目标热量及三大营养素目标。当用户询问热量目标、需要多少蛋白质/碳水/脂肪、"
    "或生成食谱方案前，应调用本工具获得科学依据。调用前先用 get_user_profile 取用户生理数据。",
    BMRInput,
    calculate_bmr_tdee,
)
