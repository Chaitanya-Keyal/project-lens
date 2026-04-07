#!/usr/bin/env python3
"""Fetch the next batch of unlinked thread digests for AI linking.

Groups telegram thread digests that have no first_message_id by week,
picks the earliest unlinked week, and outputs the thread digests plus
participant-filtered raw messages for that week.

If all threads are linked, prints ALL_LINKED as a sentinel.

Accepts --results-file to skip threads already collected in a prior run
(since the collect phase doesn't write to the DB, the sentinel check
needs another way to advance between iterations).

Usage:
    PYTHONPATH=src python scripts/telegram/ai_assess/fetch_unlinked_threads.py
    PYTHONPATH=src python scripts/telegram/ai_assess/fetch_unlinked_threads.py --results-file var/thread_links_results.json
"""
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.settings import settings
from project_lens.database import async_session
from project_lens.models import AIDigest, TelegramMsg

tg_engine = create_async_engine(
    settings.telegram_db_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.telegram_db_url else {},
)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)


def parse_source_ref(source_ref: str) -> tuple[str, str, str]:
    """Parse source_ref into (chat_id_str, date_start, date_end)."""
    parts = source_ref.split("/")
    chat_id_str = parts[0].replace("chat_", "")
    date_start, date_end = parts[1].split("_to_")
    return chat_id_str, date_start, date_end


def extract_participants(digest_text: str) -> set[str]:
    """Extract participant names from digest text."""
    for line in digest_text.split("\n"):
        if line.startswith("Participants: "):
            return {name.strip() for name in line[14:].split(", ")}
    return set()


def week_key(source_ref: str) -> str:
    """Extract the week identifier (chat_id/date_range) from source_ref."""
    parts = source_ref.split("/")
    return f"{parts[0]}/{parts[1]}"


async def main():
    # Load already-collected source_refs from results file (if provided)
    collected_refs = set()
    if "--results-file" in sys.argv:
        idx = sys.argv.index("--results-file")
        results_path = Path(sys.argv[idx + 1])
        if results_path.exists():
            with open(results_path) as f:
                for entry in json.load(f):
                    collected_refs.add(entry["source_ref"])

    # Fetch all unlinked telegram thread digests
    async with async_session() as session:
        result = await session.execute(
            select(AIDigest)
            .where(AIDigest.source_type == "telegram_thread")
            .where(AIDigest.first_message_id.is_(None))
            .order_by(AIDigest.source_ref)
        )
        digests = result.scalars().all()

    # Filter out already-collected threads
    digests = [d for d in digests if d.source_ref not in collected_refs]

    if not digests:
        print("ALL_LINKED")
        return

    # Group by week
    weeks = defaultdict(list)
    for d in digests:
        weeks[week_key(d.source_ref)].append(d)

    # Pick the earliest week
    earliest_week = sorted(weeks.keys())[0]
    batch = weeks[earliest_week]

    # Collect all participants across all threads in this week
    all_participants = set()
    for d in batch:
        all_participants.update(extract_participants(d.digest_text))

    # Parse chat_id and date range (same for all threads in the week)
    chat_id_str, date_start, date_end = parse_source_ref(batch[0].source_ref)

    # Try both chat_id forms (positive and -100 prefixed)
    chat_ids = [int(chat_id_str)]
    if not chat_id_str.startswith("-"):
        chat_ids.append(-int("100" + chat_id_str))

    # Fetch raw messages from Telegram DB
    all_msgs = []
    async with tg_session() as session:
        for cid in chat_ids:
            result = await session.execute(
                select(TelegramMsg)
                .where(TelegramMsg.chat_id == cid)
                .where(TelegramMsg.date >= date_start)
                .where(TelegramMsg.date < date_end + "T23:59:59")
                .order_by(TelegramMsg.date)
            )
            all_msgs = result.scalars().all()
            if all_msgs:
                break

    # Filter to participant messages + context (messages they replied to)
    participant_msgs = [m for m in all_msgs if m.from_user in all_participants]
    participant_msg_ids = {m.message_id for m in participant_msgs}
    reply_targets = {
        m.reply_to_message_id for m in participant_msgs
        if m.reply_to_message_id and m.reply_to_message_id not in participant_msg_ids
    }
    context_msgs = [m for m in all_msgs if m.message_id in reply_targets]
    filtered_msgs = sorted(
        participant_msgs + context_msgs,
        key=lambda m: (m.date, m.message_id),
    )

    # Count remaining unlinked weeks (excluding this one)
    remaining_weeks = len(weeks) - 1

    # Output
    print(f"=== Week: {date_start} to {date_end} ({len(batch)} threads, {len(filtered_msgs)} filtered messages, {remaining_weeks} weeks remaining) ===")
    print()

    for d in batch:
        print(f"--- Thread Digest ---")
        print(f"source_ref: {d.source_ref}")
        print()
        print(d.digest_text)
        print()

    print(f"=== Messages from participants ({len(filtered_msgs)} of {len(all_msgs)} total) ===")
    print(f"Participants: {', '.join(sorted(all_participants))}")
    print()
    for m in filtered_msgs:
        reply = f" [reply to {m.reply_to_message_id}]" if m.reply_to_message_id else ""
        ctx = " [context]" if m.message_id in reply_targets else ""
        msg_text = (m.text or "").replace("\n", " ")[:300]
        print(f"[{m.message_id}] {str(m.date)[:16]} {m.from_user or '?'}{reply}{ctx}: {msg_text}")


if __name__ == "__main__":
    asyncio.run(main())
