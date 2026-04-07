#!/usr/bin/env python3
"""Fetch the next batch of unprocessed Telegram messages with contributor context.

Groups unprocessed messages by week, picks the earliest week, and outputs
contributor context followed by the message batch with reply context.

If all messages are processed, prints ALL_PROCESSED as a sentinel.

Usage:
    PYTHONPATH=src python scripts/telegram/ai_assess/fetch_unprocessed_messages.py
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add shared scripts to path for fetch_contributor_context import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from project_lens.settings import settings
from project_lens.models import TelegramMsg
from fetch_contributor_context import get_contributor_context

tg_engine = create_async_engine(
    settings.telegram_db_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.telegram_db_url else {},
)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)


async def main():
    async with tg_session() as s:
        # Find earliest unprocessed message date
        result = await s.execute(
            select(func.min(TelegramMsg.date))
            .where(TelegramMsg.is_processed == False)
        )
        earliest = result.scalar()
        if not earliest:
            print("ALL_PROCESSED")
            return

        # Get one week's worth from that date
        batch_end = earliest + timedelta(days=7)

        # Don't process a week that hasn't closed yet — messages may still
        # arrive from the bot. Older closed weeks are processed normally;
        # only the current open week is skipped.
        now = datetime.now(earliest.tzinfo) if earliest.tzinfo else datetime.now()
        if batch_end > now:
            batch_start_str = str(earliest)[:10]
            batch_end_str = str(batch_end)[:10]
            print(f"ALL_PROCESSED")
            print(f"Current week ({batch_start_str} to {batch_end_str}) still open — waiting for it to close.")
            return

        result = await s.execute(
            select(TelegramMsg)
            .where(TelegramMsg.is_processed == False)
            .where(TelegramMsg.date >= earliest)
            .where(TelegramMsg.date < batch_end)
            .order_by(TelegramMsg.date)
        )
        msgs = result.scalars().all()

        # Find replies that point to messages outside this batch window.
        # Include those originals as context so the AI can follow the thread.
        batch_ids = {m.message_id for m in msgs}
        reply_targets = {
            m.reply_to_message_id for m in msgs
            if m.reply_to_message_id and m.reply_to_message_id not in batch_ids
        }
        context_msgs = []
        if reply_targets:
            result = await s.execute(
                select(TelegramMsg)
                .where(TelegramMsg.chat_id == msgs[0].chat_id)
                .where(TelegramMsg.message_id.in_(list(reply_targets)))
            )
            context_msgs = result.scalars().all()

        # Count remaining
        result = await s.execute(
            select(func.count(TelegramMsg.id))
            .where(TelegramMsg.is_processed == False)
        )
        remaining = result.scalar()

    # Output contributor context
    context = await get_contributor_context(include_telegram_senders=True)
    print(context)
    print()

    # Output message batch
    batch_start = str(earliest)[:10]
    batch_end_str = str(batch_end)[:10]
    print(f"Batch: {batch_start} to {batch_end_str} ({len(msgs)} messages)")
    print(f"Remaining unprocessed: {remaining}")
    print(f"Chat ID: {msgs[0].chat_id if msgs else 0}")
    print()

    if context_msgs:
        print(f"--- CONTEXT: {len(context_msgs)} referenced message(s) from outside this batch ---")
        for m in sorted(context_msgs, key=lambda x: x.message_id):
            text = (m.text or '').replace('\n', ' ')[:300]
            print(f"[{m.message_id}] {str(m.date)[:16]} {m.from_user or '?'}: {text}")
        print(f"--- END CONTEXT ---")
        print()

    for m in msgs:
        reply = f' [reply to {m.reply_to_message_id}]' if m.reply_to_message_id else ''
        media = f' [{m.message_type}]' if m.message_type != 'text' else ''
        text = (m.text or '').replace('\n', ' ')[:300]
        reactions = ''
        if m.reactions:
            parts = []
            for r in m.reactions:
                names = ', '.join(r.get('from', []))
                parts.append(f"{r['emoji']}x{r['count']}({names})")
            reactions = f' | reactions: {" ".join(parts)}'
        print(f"[{m.message_id}] {str(m.date)[:16]} {m.from_user or '?'}{reply}{media}: {text}{reactions}")

    await tg_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
