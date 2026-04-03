from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from project_lens.models.base import Base


class TelegramMsg(Base):
    """Ingested group chat messages from Telegram."""
    __tablename__ = "telegram_msgs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_user: Mapped[str | None] = mapped_column(String, nullable=True)
    from_username: Mapped[str | None] = mapped_column(String, nullable=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str | None] = mapped_column(String, nullable=True)
    message_type: Mapped[str] = mapped_column(String, default="text")
    reactions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted_actions: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_chat_message"),
    )
