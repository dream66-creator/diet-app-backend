"""饮食打卡记录表：Agent 拆解自然语言/图片后，逐条落库。

关键设计：
- 一餐拆成多行（每行一种食物），按 (user_id, eaten_at) 聚合即可还原整餐；
- food_name 与营养值为「快照」：食物库日后修订不影响历史数据；
- food_id 可空：拍照识别或食物库外的食物只有名称快照；
- 时间存本地 naive datetime（MVP 简化，单时区单用户场景）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class DietRecord(db.Model):
    __tablename__ = "diet_records"
    __table_args__ = (db.Index("ix_diet_records_user_eaten", "user_id", "eaten_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, db.ForeignKey("users.id"), index=True)

    meal_type: Mapped[str] = mapped_column(String(16), index=True)
    # breakfast 早餐 / lunch 午餐 / dinner 晚餐 / snack 加餐

    food_id: Mapped[Optional[int]] = mapped_column(Integer, db.ForeignKey("foods.id"))
    food_name: Mapped[str] = mapped_column(String(128))  # 名称快照
    amount_g: Mapped[float] = mapped_column(Float)  # 实际食用克数

    # ---- 营养快照（落库时换算，随记录固化） ----
    calories_kcal: Mapped[float] = mapped_column(Float, default=0)
    protein_g: Mapped[float] = mapped_column(Float, default=0)
    fat_g: Mapped[float] = mapped_column(Float, default=0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0)
    fiber_g: Mapped[float] = mapped_column(Float, default=0)
    sodium_mg: Mapped[Optional[float]] = mapped_column(Float)

    source: Mapped[str] = mapped_column(String(16), default="nlp")
    # nlp 自然语言 / manual 手动 / barcode 扫码 / photo 拍照 / favorite 收藏一键打卡

    eaten_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
