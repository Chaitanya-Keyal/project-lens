#!/usr/bin/env python3
"""Fetch contributor profiles and project context for AI assessment prompts.

Shared across all prefetch scripts. Outputs contributor tier scores and
optionally merge authorities and/or top Telegram senders.

Usage:
    PYTHONPATH=src python scripts/shared/fetch_contributor_context.py
    PYTHONPATH=src python scripts/shared/fetch_contributor_context.py --merge-authorities
    PYTHONPATH=src python scripts/shared/fetch_contributor_context.py --telegram-senders
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from project_lens.database import async_session
from project_lens.models import ContributorProfile, ProjectConfigEntry, TelegramMsg
from project_lens.settings import settings


async def get_contributor_context(
    include_merge_authorities: bool = False,
    include_telegram_senders: bool = False,
) -> str:
    """Fetch contributor context as formatted text for prompt injection."""
    lines = []

    async with async_session() as s:
        result = await s.execute(
            select(ContributorProfile)
            .order_by(ContributorProfile.last_active.desc())
        )
        lines.append("=== Contributor Profiles ===")
        for p in result.scalars():
            s_ = p.stats
            total = s_.get('total_activity', s_.get('merge_commits', 0) + s_.get('prs_merged', 0) + s_.get('total_review_engagement', 0))
            if total < 5:
                continue
            pt = s_.get('pr_tier', 'none')
            rt = s_.get('review_tier', 'none')
            tt = s_.get('total_tier', 'none')
            gt = s_.get('group_tier', 'none')
            login_str = p.github_login
            if include_telegram_senders and p.display_names:
                names = ', '.join(n for n in p.display_names if n != p.github_login)
                if names:
                    login_str += f" (names: {names})"
            lines.append(
                f"{login_str} — shipped:{pt} reviews:{rt} total:{tt} group:{gt}"
                f" — {s_.get('prs_merged', 0)} PRs merged,"
                f" {s_.get('total_review_engagement', 0)} review engagements,"
                f" {total} total"
            )

        if include_merge_authorities:
            result = await s.execute(
                select(ProjectConfigEntry).where(ProjectConfigEntry.key == 'merge_authorities')
            )
            entry = result.scalar_one_or_none()
            if entry:
                lines.append("")
                lines.append("=== Merge Authorities ===")
                for ma in entry.value:
                    lines.append(f"{ma['login']} — scope: {ma['scope']} — {ma.get('description', '')}")

    if include_telegram_senders:
        tg_engine = create_async_engine(
            settings.telegram_db_url,
            connect_args={"check_same_thread": False} if "sqlite" in settings.telegram_db_url else {},
        )
        tg_session_factory = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)
        async with tg_session_factory() as s:
            result = await s.execute(
                select(TelegramMsg.from_user, func.count(TelegramMsg.id))
                .where(TelegramMsg.from_user.is_not(None))
                .group_by(TelegramMsg.from_user)
                .order_by(func.count(TelegramMsg.id).desc())
                .limit(25)
            )
            lines.append("")
            lines.append("=== Top Telegram Senders ===")
            for name, count in result.all():
                lines.append(f"{name}: {count} messages")
        await tg_engine.dispose()

    return "\n".join(lines)


async def main():
    text = await get_contributor_context(
        include_merge_authorities="--merge-authorities" in sys.argv,
        include_telegram_senders="--telegram-senders" in sys.argv,
    )
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
