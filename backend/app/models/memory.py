"""对话消息表：Agent 短期记忆（跨请求续聊）与工具调用轨迹。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class ConversationMessage(db.Model):
    __tablename__ = "conversation_messages"
    __table_args__ = (db.Index("ix_conv_user_session_time", "user_id", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, db.ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)

    role: Mapped[str] = mapped_column(String(16))  # user / assistant / tool
    content: Mapped[str] = mapped_column(Text)

    tool_calls: Mapped[Optional[list]] = mapped_column(JSON)
    # assistant 消息的工具调用轨迹:
    # [{"id": "call_xxx", "name": "search_food_database", "arguments": {...}}]
    # 用于可解释性调试与上下文重建（arguments 为 dict，重建时 json.dumps）
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(64))
    # tool 角色的消息：关联的 tool_call id，多轮对话重建时必需

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
