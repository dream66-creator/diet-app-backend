"""用户档案：既是账号信息，也是 Agent 长期记忆的载体。

关键设计：
- dietary_preferences（JSON 列）：忌口、过敏原、口味偏好，Agent 生成食谱前必读；
- bmr / tdee / target_*：calculate_bmr_tdee 计算结果的缓存，
  避免每次对话重复计算；体重变化后由 Agent 触发重算并覆盖；
- to_dict()：Agent 读取长期记忆时的标准视图（不含密码等敏感字段）。
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[Optional[str]] = mapped_column(String(64))

    # ---- 生理信息（calculate_bmr_tdee 的输入） ----
    gender: Mapped[str] = mapped_column(String(8), default="unknown")  # male / female / unknown
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    height_cm: Mapped[Optional[float]] = mapped_column(Float)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)

    # ---- 目标与活动量 ----
    activity_level: Mapped[str] = mapped_column(String(16), default="light")
    # sedentary 久坐 / light 轻度 / moderate 中度 / active 高度 / very_active 极高
    goal: Mapped[str] = mapped_column(String(16), default="maintain")
    # lose_fat 减脂 / gain_muscle 增肌 / maintain 维持 / control_sugar 控糖

    # ---- 长期记忆：口味、忌口、过敏原、预算 ----
    dietary_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    # 例: {"vegetarian": true, "lactose_intolerant": false,
    #      "allergies": ["花生"], "avoid": ["香菜", "内脏"], "prefer": ["清淡", "微辣"]}
    budget_per_day: Mapped[Optional[float]] = mapped_column(Float)  # 每日餐食预算（元）

    # ---- Agent 计算结果缓存 ----
    bmr: Mapped[Optional[float]] = mapped_column(Float)
    tdee: Mapped[Optional[float]] = mapped_column(Float)
    target_calories: Mapped[Optional[float]] = mapped_column(Float)
    target_protein_g: Mapped[Optional[float]] = mapped_column(Float)
    target_fat_g: Mapped[Optional[float]] = mapped_column(Float)
    target_carbs_g: Mapped[Optional[float]] = mapped_column(Float)
    water_goal_ml: Mapped[int] = mapped_column(Integer, default=2000)
    targets_customized: Mapped[bool] = mapped_column(Boolean, default=False)
    # 用户是否手动自定义了热量/营养素目标；True 时公式重算不再自动覆盖

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        """Agent 读取长期记忆时的标准视图。"""
        return {
            "user_id": self.id,
            "nickname": self.nickname,
            "gender": self.gender,
            "age": None if not self.birth_date else _age(self.birth_date),
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "activity_level": self.activity_level,
            "goal": self.goal,
            "dietary_preferences": self.dietary_preferences,
            "budget_per_day": self.budget_per_day,
            "targets": {
                "bmr": self.bmr,
                "tdee": self.tdee,
                "calories": self.target_calories,
                "protein_g": self.target_protein_g,
                "fat_g": self.target_fat_g,
                "carbs_g": self.target_carbs_g,
                "water_goal_ml": self.water_goal_ml,
                "customized": self.targets_customized,
            },
        }


def _age(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


class WeightLog(db.Model):
    """体重记录：Agent 观察趋势、判断是否需要重算目标。"""
    __tablename__ = "weight_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, db.ForeignKey("users.id"), index=True)
    weight_kg: Mapped[float] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
