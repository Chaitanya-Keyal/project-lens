#!/usr/bin/env python3
"""Ingest Telegram group messages into the project-lens database.

Two ingestion modes:

  1. JSON import (historical bulk load):
     Export chat history from Telegram Desktop (Settings > Advanced > Export chat history,
     format: Machine-readable JSON). Then import:

       python scripts/telegram_ingest.py --import-json /path/to/result.json

  2. Bot polling (ongoing new messages):
     Add the bot to the group, then run:

       python scripts/telegram_ingest.py --poll

     The bot listens for new messages via long polling and stores them in real time.
     It does NOT post messages — read-only ingestion only.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (polling mode).
JSON import only requires TELEGRAM_CHAT_ID if you want to override the chat_id from
the export file.

Usage:
    python scripts/telegram_ingest.py --import-json <path>   # bulk historical import
    python scripts/telegram_ingest.py --poll                  # live polling (blocking)
    python scripts/telegram_ingest.py --stats                 # show ingestion stats
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.settings import settings
from project_lens.database import engine as main_engine, async_session as main_session
from project_lens.models import Base, TelegramMsg, AnalysisRun

# Telegram messages live in a separate database so they can be selectively shared.
# The main DB (project-lens.db) holds GitHub data, assessments, and run logs.
# The telegram DB (telegram.db) holds only TelegramMsg records.
tg_engine = create_async_engine(
    settings.telegram_db_url,
    echo=settings.debug,
    connect_args={"check_same_thread": False} if "sqlite" in settings.telegram_db_url else {},
)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def flatten_text(text_field) -> str:
    """Convert Telegram export text field to plain string.

    In Telegram Desktop JSON exports, the 'text' field can be:
      - A plain string: "hello"
      - A list of mixed entities: ["hello ", {"type": "bold", "text": "world"}, "!"]
    This flattens both forms to a single string.
    """
    if isinstance(text_field, str):
        return text_field
    if isinstance(text_field, list):
        parts = []
        for chunk in text_field:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                parts.append(chunk.get("text", ""))
        return "".join(parts)
    return ""


def parse_export_date(date_str: str) -> datetime:
    """Parse date string from Telegram Desktop export.

    Format is typically "2022-12-01T10:00:00" (naive, in the exporter's local tz).
    We treat it as UTC for consistency.
    """
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def classify_message_type(msg: dict) -> str:
    """Determine message type from Telegram Desktop export message dict."""
    if msg.get("photo"):
        return "photo"
    if msg.get("file"):
        mime = msg.get("mime_type", "")
        if "video" in mime:
            return "video"
        if "audio" in mime or "voice" in mime:
            return "voice"
        return "document"
    if msg.get("sticker_emoji"):
        return "sticker"
    if msg.get("poll"):
        return "poll"
    if msg.get("location_information"):
        return "location"
    if msg.get("contact_information"):
        return "contact"
    return "text"


def parse_from_id(from_id_str: str | None) -> int | None:
    """Extract numeric user ID from Telegram export's from_id field.

    Export format uses strings like "user123456789" or "channel123456789".
    """
    if not from_id_str:
        return None
    # Strip known prefixes
    for prefix in ("user", "channel"):
        if from_id_str.startswith(prefix):
            try:
                return int(from_id_str[len(prefix):])
            except ValueError:
                pass
    # Try raw int
    try:
        return int(from_id_str)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# JSON Import (historical bulk load)
# ---------------------------------------------------------------------------

async def import_json(json_path: str):
    """Import messages from a Telegram Desktop JSON export file."""
    path = Path(json_path)
    if not path.exists():
        print(f"File not found: {json_path}")
        sys.exit(1)

    print(f"Loading {json_path}...")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # The export has a top-level "messages" array and chat metadata
    chat_name = data.get("name", "Unknown")
    chat_type = data.get("type", "unknown")
    export_chat_id = data.get("id", 0)
    messages = data.get("messages", [])

    # Use configured chat_id if set, otherwise use the export's ID
    chat_id = settings.telegram_chat_id if settings.telegram_chat_id else export_chat_id

    print(f"Chat: {chat_name} ({chat_type})")
    print(f"Chat ID: {chat_id}")
    print(f"Messages in export: {len(messages)}")
    print(f"Telegram DB: {settings.telegram_db_url}")

    # Ensure telegram_msgs table exists in the telegram DB (only this table)
    async with tg_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[TelegramMsg.__table__])
        )

    # Find what we already have for this chat
    async with tg_session() as session:
        result = await session.execute(
            select(func.count(TelegramMsg.id)).where(TelegramMsg.chat_id == chat_id)
        )
        existing_count = result.scalar()
        print(f"Existing messages in DB for this chat: {existing_count}")

        # Get set of already-imported message IDs for fast dedup
        result = await session.execute(
            select(TelegramMsg.message_id).where(TelegramMsg.chat_id == chat_id)
        )
        existing_ids = {row[0] for row in result.all()}

    # Filter to actual messages (skip service messages like "joined the group")
    importable = [m for m in messages if m.get("type") == "message"]
    new_messages = [m for m in importable if m.get("id") not in existing_ids]

    print(f"Importable messages: {len(importable)} (skipping {len(messages) - len(importable)} service messages)")
    print(f"New messages to import: {len(new_messages)}")

    if not new_messages:
        print("Nothing to import.")
        return

    run_start = datetime.now(timezone.utc)
    imported = 0
    skipped = 0

    # Batch insert in chunks
    BATCH_SIZE = 500
    for i in range(0, len(new_messages), BATCH_SIZE):
        batch = new_messages[i:i + BATCH_SIZE]
        async with tg_session() as session:
            for msg in batch:
                text = flatten_text(msg.get("text", ""))
                # Skip empty messages with no text and no media
                if not text and not msg.get("photo") and not msg.get("file") and not msg.get("sticker_emoji"):
                    skipped += 1
                    continue

                # Parse reactions: [{emoji, count, from: [names]}]
                raw_reactions = msg.get("reactions")
                reactions = None
                if raw_reactions:
                    reactions = []
                    for r in raw_reactions:
                        entry = {
                            "emoji": r.get("emoji", ""),
                            "count": r.get("count", 0),
                            "from": [p.get("from", "") for p in r.get("recent", [])],
                        }
                        reactions.append(entry)

                telegram_msg = TelegramMsg(
                    chat_id=chat_id,
                    message_id=msg["id"],
                    from_user_id=parse_from_id(msg.get("from_id")),
                    from_user=msg.get("from", None),
                    from_username=None,  # Not in Desktop export format
                    reply_to_message_id=msg.get("reply_to_message_id"),
                    text=text if text else None,
                    message_type=classify_message_type(msg),
                    reactions=reactions,
                    date=parse_export_date(msg.get("date", "")),
                    is_processed=False,
                )
                session.add(telegram_msg)
                imported += 1

            await session.commit()

        print(f"  Imported {min(i + BATCH_SIZE, len(new_messages))}/{len(new_messages)}...")

    # Record the analysis run in the main DB (run log lives with the rest of the project)
    async with main_session() as session:
        run = AnalysisRun(
            run_type="telegram_import",
            started_at=run_start,
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=f"Imported {imported} messages from JSON export ({chat_name}). Skipped {skipped} empty.",
        )
        session.add(run)
        await session.commit()

    elapsed = datetime.now(timezone.utc) - run_start
    print(f"\nDone in {elapsed}. Imported {imported} messages, skipped {skipped} empty.")


# ---------------------------------------------------------------------------
# Bot Polling (ongoing new messages)
# ---------------------------------------------------------------------------

async def poll():
    """Run the Telegram bot in long-polling mode, ingesting new messages."""
    try:
        from telegram import Update
        from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
    except ImportError:
        print("python-telegram-bot not installed. Run: pip install 'python-telegram-bot>=21.0'")
        sys.exit(1)

    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    if not settings.telegram_chat_id:
        print("TELEGRAM_CHAT_ID not set in .env")
        sys.exit(1)

    # Ensure telegram_msgs table exists in the telegram DB
    async with tg_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[TelegramMsg.__table__])
        )

    target_chat_id = settings.telegram_chat_id

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Store incoming messages from the target group."""
        msg = update.effective_message
        if not msg:
            return

        # Only ingest from the configured chat
        if msg.chat_id != target_chat_id:
            return

        user = msg.from_user
        text = msg.text or msg.caption or ""

        # Determine message type
        if msg.photo:
            msg_type = "photo"
        elif msg.video:
            msg_type = "video"
        elif msg.document:
            msg_type = "document"
        elif msg.voice or msg.audio:
            msg_type = "voice"
        elif msg.sticker:
            msg_type = "sticker"
        elif msg.poll:
            msg_type = "poll"
        else:
            msg_type = "text"

        telegram_msg = TelegramMsg(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            from_user_id=user.id if user else None,
            from_user=user.full_name if user else None,
            from_username=user.username if user else None,
            reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
            text=text if text else None,
            message_type=msg_type,
            date=msg.date.replace(tzinfo=timezone.utc) if msg.date else datetime.now(timezone.utc),
            is_processed=False,
        )

        try:
            async with tg_session() as session:
                # Check for duplicate (bot might receive same update twice)
                result = await session.execute(
                    select(TelegramMsg.id).where(
                        TelegramMsg.chat_id == msg.chat_id,
                        TelegramMsg.message_id == msg.message_id,
                    )
                )
                if result.scalar_one_or_none() is None:
                    session.add(telegram_msg)
                    await session.commit()
                    print(f"  [{msg.date}] {user.full_name if user else '?'}: {text[:80]}...")
        except Exception as e:
            print(f"  Error storing message {msg.message_id}: {e}")

    print(f"Starting Telegram bot polling...")
    print(f"Listening for messages in chat {target_chat_id}")
    print(f"Press Ctrl+C to stop.\n")

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Handle all message types (text, photo, document, etc.)
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    # Run with polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)

    # Keep running until interrupted
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\nShutting down...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

async def show_stats():
    """Show ingestion statistics."""
    async with tg_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[TelegramMsg.__table__])
        )

    async with tg_session() as session:
        # Total messages
        result = await session.execute(select(func.count(TelegramMsg.id)))
        total = result.scalar()

        # Per chat
        result = await session.execute(
            select(TelegramMsg.chat_id, func.count(TelegramMsg.id))
            .group_by(TelegramMsg.chat_id)
        )
        chats = result.all()

        # Date range
        result = await session.execute(
            select(func.min(TelegramMsg.date), func.max(TelegramMsg.date))
        )
        date_range = result.one()

        # Unprocessed
        result = await session.execute(
            select(func.count(TelegramMsg.id)).where(TelegramMsg.is_processed == False)  # noqa: E712
        )
        unprocessed = result.scalar()

        # Top senders
        result = await session.execute(
            select(TelegramMsg.from_user, func.count(TelegramMsg.id))
            .group_by(TelegramMsg.from_user)
            .order_by(func.count(TelegramMsg.id).desc())
            .limit(10)
        )
        top_senders = result.all()

        # Message types
        result = await session.execute(
            select(TelegramMsg.message_type, func.count(TelegramMsg.id))
            .group_by(TelegramMsg.message_type)
            .order_by(func.count(TelegramMsg.id).desc())
        )
        types = result.all()

    print(f"Telegram Ingestion Stats")
    print(f"{'=' * 40}")
    print(f"Total messages: {total}")
    print(f"Unprocessed:    {unprocessed}")
    if date_range[0]:
        print(f"Date range:     {str(date_range[0])[:10]} to {str(date_range[1])[:10]}")
    print()

    if chats:
        print(f"Chats:")
        for chat_id, count in chats:
            print(f"  {chat_id}: {count} messages")
        print()

    if top_senders:
        print(f"Top senders:")
        for name, count in top_senders:
            print(f"  {name or '(unknown)'}: {count}")
        print()

    if types:
        print(f"Message types:")
        for msg_type, count in types:
            print(f"  {msg_type}: {count}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    if "--import-json" in sys.argv:
        idx = sys.argv.index("--import-json")
        if idx + 1 >= len(sys.argv):
            print("Usage: telegram_ingest.py --import-json <path>")
            sys.exit(1)
        await import_json(sys.argv[idx + 1])

    elif "--poll" in sys.argv:
        await poll()

    elif "--stats" in sys.argv:
        await show_stats()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
