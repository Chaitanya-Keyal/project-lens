#!/bin/bash
# Run Telegram digest in a loop — one batch per claude -p invocation.
# Fresh context each time, no wasted tokens on accumulated history.
# Each run picks up where the last left off via is_processed flags.
#
# Usage: ./scripts/run_telegram_digest.sh

set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

batch_count=0

while true; do
    # Check if there's anything left to process
    remaining=$(DEBUG=false python -c "
import asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from backend.config import settings
from backend.models import TelegramMsg
tg_engine = create_async_engine(settings.telegram_db_url)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)
async def check():
    async with tg_session() as s:
        r = await s.execute(select(func.count(TelegramMsg.id)).where(TelegramMsg.is_processed == False))
        print(r.scalar())
asyncio.run(check())
")

    if [ "$remaining" = "0" ]; then
        echo "All messages processed after $batch_count batches."
        break
    fi

    batch_count=$((batch_count + 1))
    echo "=== Batch $batch_count ($remaining messages remaining) ==="
    claude -p "$(cat scripts/telegram_digest_prompt.md)" || true
done
