#!/usr/bin/env python3
"""Write Telegram discussion digests and mark messages as processed.

Called by the Claude Code batch job after AI analysis of a message batch.
Writes thread summaries to ai_digests and decisions to the decisions table
(both in the main DB), then marks source message IDs as processed in the
Telegram DB.

Usage:
    python scripts/write_telegram_digest.py --file /tmp/telegram_digest.json
"""
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.database import engine as main_engine, async_session as main_session
from backend.models import Base, AIDigest, TelegramMsg, AnalysisRun

# Telegram DB (separate file)
tg_engine = create_async_engine(
    settings.telegram_db_url,
    echo=settings.debug,
    connect_args={"check_same_thread": False} if "sqlite" in settings.telegram_db_url else {},
)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)


async def write_batch(batch: dict):
    """Write one batch of digested threads and decisions."""
    batch_start = batch["batch_start"]
    batch_end = batch["batch_end"]
    chat_id = batch.get("chat_id", settings.telegram_chat_id)
    threads = batch.get("threads", [])
    all_message_ids = set()
    now = datetime.now(timezone.utc)

    # Collect all message IDs referenced by threads
    for thread in threads:
        for mid in thread.get("message_ids", []):
            all_message_ids.add(mid)

    # Also mark any explicitly listed discarded message IDs
    for mid in batch.get("processed_message_ids", []):
        all_message_ids.add(mid)

    print(f"  Batch {batch_start} to {batch_end}: {len(threads)} threads, {len(all_message_ids)} messages")

    # Write thread summaries and decisions to main DB
    async with main_session() as session:
        for i, thread in enumerate(threads):
            topic = thread["topic"]
            category = thread.get("category", "project_broad")
            summary = thread["summary"]
            participants = thread.get("participants", [])
            pr_refs = thread.get("pr_references", [])
            issue_refs = thread.get("issue_references", [])
            message_ids = thread.get("message_ids", [])

            # Build the full digest text
            digest_parts = [
                f"Topic: {topic}",
                f"Category: {category}",
                f"Period: {batch_start} to {batch_end}",
                f"Participants: {', '.join(participants)}",
            ]
            if pr_refs:
                digest_parts.append(f"PR references: {', '.join(pr_refs)}")
            if issue_refs:
                digest_parts.append(f"Issue references: {', '.join(issue_refs)}")
            digest_parts.append(f"\n{summary}")

            # Add key points if present
            for kp in thread.get("key_points", []):
                digest_parts.append(f"- {kp}")

            # Include per-person takes in the digest text so they get embedded
            # alongside the thread summary for semantic search
            for take in thread.get("per_person_takes", []):
                digest_parts.append(f"- {take['person']}: {take['gist']}")

            digest_text = "\n".join(digest_parts)

            # Content hash for dedup/change detection
            content_hash = hashlib.sha256(
                json.dumps({
                    "message_ids": sorted(message_ids),
                    "topic": topic,
                }, sort_keys=True).encode()
            ).hexdigest()

            # Source ref: chat/date-range/thread-index
            source_ref = f"chat_{chat_id}/{batch_start}_to_{batch_end}/thread_{i}"

            # Upsert ai_digest
            result = await session.execute(
                select(AIDigest).where(
                    AIDigest.source_type == "telegram_thread",
                    AIDigest.source_ref == source_ref,
                    AIDigest.digest_type == "summary",
                )
            )
            existing = result.scalar_one_or_none()

            digest_fields = {
                "content_hash": content_hash,
                "digest_text": digest_text,
                "model": batch.get("model", "claude-opus-4-6"),
                "input_tokens": None,
                "output_tokens": None,
            }

            if existing:
                for k, v in digest_fields.items():
                    setattr(existing, k, v)
                existing.updated_at = now
            else:
                session.add(AIDigest(
                    source_type="telegram_thread",
                    source_ref=source_ref,
                    digest_type="summary",
                    **digest_fields,
                ))

        await session.commit()

    # Mark messages as processed in telegram DB
    if all_message_ids:
        async with tg_session() as session:
            await session.execute(
                update(TelegramMsg)
                .where(
                    TelegramMsg.chat_id == chat_id,
                    TelegramMsg.message_id.in_(all_message_ids),
                )
                .values(is_processed=True)
            )
            await session.commit()

    print(f"    Wrote {len(threads)} digests, marked {len(all_message_ids)} messages processed")


async def main():
    if "--file" not in sys.argv:
        print("Usage: write_telegram_digest.py --file <path>")
        sys.exit(1)

    idx = sys.argv.index("--file")
    filepath = sys.argv[idx + 1]
    with open(filepath) as f:
        data = json.load(f)

    # Accept a single batch or array of batches
    batches = data if isinstance(data, list) else [data]

    # Validate every batch before writing anything
    from scripts.validate_telegram_digest import validate
    all_errors = []
    for i, batch in enumerate(batches):
        errors = validate(batch)
        for e in errors:
            all_errors.append(f"batch[{i}]: {e}")
    if all_errors:
        print(f"VALIDATION FAILED — {len(all_errors)} error(s):")
        for e in all_errors:
            print(f"  - {e}")
        print(f"\nFix the errors in {filepath} and re-run.")
        sys.exit(1)
    print(f"Validation passed for {len(batches)} batch(es).")

    # Ensure tables exist
    async with main_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run_start = datetime.now(timezone.utc)
    total_threads = 0

    for batch in batches:
        await write_batch(batch)
        total_threads += len(batch.get("threads", []))

    # Record the analysis run
    async with main_session() as session:
        session.add(AnalysisRun(
            run_type="telegram_digest",
            started_at=run_start,
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=f"Digested {len(batches)} batch(es): {total_threads} threads.",
        ))
        await session.commit()

    print(f"\nDone: {len(batches)} batch(es), {total_threads} threads.")


if __name__ == "__main__":
    asyncio.run(main())
