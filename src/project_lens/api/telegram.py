"""Telegram data access for LensClient."""

from sqlalchemy import select

from project_lens.models import TelegramMsg
from project_lens.api.config import LensConfig


class TelegramService:
    """Read-only access to Telegram message data."""

    def __init__(self, config: LensConfig):
        self._config = config

    async def list_participants(self) -> list[str]:
        """Distinct Telegram display names from message history."""
        async with self._config.telegram_session() as session:
            result = await session.execute(
                select(TelegramMsg.from_user)
                .where(TelegramMsg.from_user.isnot(None))
                .distinct()
                .order_by(TelegramMsg.from_user)
            )
            return [row[0] for row in result.all()]
