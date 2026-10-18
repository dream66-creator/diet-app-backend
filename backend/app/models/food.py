"""食物库：内置家常菜/外卖/生鲜/包装食品，可扩展用户自建食物。

营养字段统一为「每 100g 含量」，打卡时按实际克数等比缩放，
避免每种食物保存多份不同份量的营养数据。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class Food(db.Model):
    __tablename__ = "foods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # 中文别名，如 ["白米饭", "大米饭"]
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    # staple 主食 / vegetable 蔬菜 / meat 肉类 / fish 水产 / dairy 奶制品 /
    # fruit 水果 / snack 零食 / drink 饮品 / restaurant 外卖 / processed 包装食品 / other

    unit: Mapped[str] = mapped_column(String(16), default="g")  # g / 份 / 碗 / 杯 / 个
    default_portion_g: Mapped[Optional[float]] = mapped_column(Float)  # 1 份的重量（克）

    # ---- 每 100g 营养 ----
    calories_kcal_per_100g: Mapped[float] = mapped_column(Float, default=0)
    protein_g_per_100g: Mapped[float] = mapped_column(Float, default=0)
    fat_g_per_100g: Mapped[float] = mapped_column(Float, default=0)
    carbs_g_per_100g: Mapped[float] = mapped_column(Float, default=0)
    fiber_g_per_100g: Mapped[float] = mapped_column(Float, default=0)
    sodium_mg_per_100g: Mapped[Optional[float]] = mapped_column(Float)

    # ---- 包装食品条码（fetch_barcode_info 的本地数据源） ----
    barcode: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(64))

    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(16), default="builtin")  # builtin / custom
    created_by: Mapped[Optional[int]] = mapped_column(Integer, db.ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
