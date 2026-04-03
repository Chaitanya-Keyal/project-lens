#!/bin/bash
# Run PR assessment in a loop — one batch per claude -p invocation.
# Fresh context each time, no wasted tokens on accumulated history.
# Each run picks up where the last left off (unassessed PRs query).
#
# Usage: ./scripts/github/ai_assess/batch_assess.sh
#
# Logs to var/batch_assess.log

set -e
cd "$(dirname "$0")/../../.."
source .venv/bin/activate

mkdir -p var

batch_count=0

while true; do
    # Check if there are unassessed PRs remaining
    remaining=$(PYTHONPATH=src DEBUG=false python -c "
import asyncio
from project_lens.database import async_session
from project_lens.models import GithubPR, PRAssessment
from sqlalchemy import select

async def check():
    async with async_session() as s:
        assessed = await s.execute(select(PRAssessment.repo, PRAssessment.pr_number))
        assessed_set = {(r[0], r[1]) for r in assessed.all()}
        result = await s.execute(
            select(GithubPR)
            .where(GithubPR.detail_fetched_at.is_not(None))
        )
        prs = [p for p in result.scalars().all() if (p.repo, p.number) not in assessed_set]
        print(len(prs))

asyncio.run(check())
")

    if [ "$remaining" = "0" ]; then
        echo "$(date): All PRs assessed after $batch_count batches." >> var/batch_assess.log
        echo "All PRs assessed after $batch_count batches."
        break
    fi

    batch_count=$((batch_count + 1))
    echo "$(date): Starting batch $batch_count ($remaining PRs remaining)" >> var/batch_assess.log

    claude -p \
      --permission-mode bypassPermissions \
      --max-turns 50 \
      "$(cat scripts/github/ai_assess/batch_assess_prompt.md)" \
      >> var/batch_assess.log 2>&1 || true

    echo "$(date): Batch $batch_count complete" >> var/batch_assess.log
done
