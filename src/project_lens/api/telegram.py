"""Telegram data access for LensClient."""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from project_lens.models import AIDigest, TelegramMsg
from project_lens.api.config import LensConfig


@dataclass
class TelegramDigestSummary:
    """A Telegram thread digest for feed display."""
    chroma_id: str        # "tg_" + source_ref — used for detail page links
    topic: str
    category: str
    period: str
    summary: str          # first narrative sentence after header lines
    created_at: datetime
    updated_at: datetime


def _parse_digest_metadata(digest_text: str) -> dict:
    """Extract topic, category, period, and summary from digest header lines.

    Digest text format:
        Topic: <topic>
        Period: <period>
        Category: <category>

        <narrative body...>
    """
    topic = ""
    period = ""
    category = ""
    body_lines = []
    past_headers = False

    for line in digest_text.split("\n"):
        if not past_headers:
            if line.startswith("Topic: "):
                topic = line[7:]
            elif line.startswith("Period: "):
                period = line[8:]
            elif line.startswith("Category: "):
                category = line[10:]
            elif line.strip() == "" and (topic or period or category):
                past_headers = True
        else:
            stripped = line.strip()
            if stripped and not stripped.startswith("- "):
                body_lines.append(stripped)

    # First sentence of narrative body as summary
    narrative = " ".join(body_lines)
    summary = ""
    if narrative:
        m = re.match(r"^(.+?\.)\s", narrative)
        summary = m.group(1) if m else narrative[:200]

    return {
        "topic": topic,
        "category": category,
        "period": period,
        "summary": summary,
    }


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

    async def get_recent_digests(
        self,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[TelegramDigestSummary]:
        """Recent Telegram thread digests, ordered by updated_at DESC.

        Queries the AIDigest table (main DB) for telegram_thread digests,
        parses metadata from the digest text header lines.
        """
        filters = [AIDigest.source_type == "telegram_thread"]
        if since:
            filters.append(AIDigest.updated_at >= since)

        async with self._config.async_session() as session:
            result = await session.execute(
                select(AIDigest)
                .where(*filters)
                .order_by(AIDigest.updated_at.desc())
                .limit(limit)
            )
            digests = []
            for d in result.scalars():
                meta = _parse_digest_metadata(d.digest_text)
                digests.append(TelegramDigestSummary(
                    chroma_id=f"tg_{d.source_ref}",
                    topic=meta["topic"],
                    category=meta["category"],
                    period=meta["period"],
                    summary=meta["summary"],
                    created_at=d.created_at,
                    updated_at=d.updated_at,
                ))
            return digests
