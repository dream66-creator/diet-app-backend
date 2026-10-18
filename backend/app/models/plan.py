"""食谱方案与常用餐食收藏：日历视图（按天展示计划）与一键打卡的数据来源。"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class MealPlan(db.Model):
    """每日饮食方案（Agent 通过 generate_meal_plan 生成，Web 端日历视图展示/编辑）。"""
    __tablename__ = "meal_plans"
    __table_args__ = (UniqueConstraint("user_id", "plan_date", name="uq_meal_plan_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, db.ForeignKey("users.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    target_calories_kcal: Mapped[float] = mapped_column(Float)

    content: Mapped[dict] = mapped_column(JSON)
    # {"breakfast": [{"food_id": 1, "food_name": "…", "amount_g": 250, "calories_kcal": 290,
    #                 "protein_g": 8.0, "fat_g": 1.5, "carbs_g": 65.0}, ...],
    #  "lunch": [...], "dinner": [...], "snack": [...]}

    note: Mapped[Optional[str]] = mapped_column(Text)  # Agent 的搭配说明

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class FavoriteMeal(db.Model):
    """常用餐食收藏：一键快速打卡（record_diet_to_db 的 favorite 来源）。"""
    __tablename__ = "favorite_meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, db.ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))

    items: Mapped[list] = mapped_column(JSON)
    # [{"food_id": 1, "food_name": "…", "amount_g": 200, "calories_kcal": 380,
    #   "protein_g": 28.0, "fat_g": 24.0, "carbs_g": 12.0}, ...]

    total_calories_kcal: Mapped[float] = mapped_column(Float, default=0)  # 缓存合计，列表展示用

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
