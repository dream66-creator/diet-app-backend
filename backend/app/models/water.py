"""饮水记录表：add_water_intake 工具落库，首页仪表盘展示今日进度。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class WaterRecord(db.Model):
    __tablename__ = "water_records"
    __table_args__ = (db.Index("ix_water_records_user_drank", "user_id", "drank_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, db.ForeignKey("users.id"), index=True)
    volume_ml: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="agent")  # agent / manual
    note: Mapped[Optional[str]] = mapped_column(String(255))
    drank_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
