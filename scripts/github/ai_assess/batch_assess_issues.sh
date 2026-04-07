#!/bin/bash
# Run issue assessment in a loop — one batch per claude -p invocation.
# Fresh context each time, no wasted tokens on accumulated history.
# Each run picks up where the last left off (unassessed issues query).
#
# Usage: ./scripts/github/ai_assess/batch_assess_issues.sh
#
# Logs to var/batch_assess_issues.log

set -e
cd "$(dirname "$0")/../../.."
source .venv/bin/activate

mkdir -p var

batch_count=0

while true; do
    # Check if there are unassessed issues remaining
    remaining=$(PYTHONPATH=src DEBUG=false python -c "
import asyncio
from project_lens.database import async_session
from project_lens.models import GithubIssue, IssueAssessment
from sqlalchemy import select

async def check():
    async with async_session() as s:
        assessed = await s.execute(select(IssueAssessment.repo, IssueAssessment.issue_number))
        assessed_set = {(r[0], r[1]) for r in assessed.all()}
        result = await s.execute(
            select(GithubIssue)
            .where(GithubIssue.detail_fetched_at.is_not(None))
        )
        issues = [i for i in result.scalars().all() if (i.repo, i.number) not in assessed_set]
        print(len(issues))

asyncio.run(check())
")

    if [ "$remaining" = "0" ]; then
        echo "$(date): All issues assessed after $batch_count batches." >> var/batch_assess_issues.log
        echo "All issues assessed after $batch_count batches."
        break
    fi

    batch_count=$((batch_count + 1))
    echo "$(date): Starting batch $batch_count ($remaining issues remaining)" >> var/batch_assess_issues.log

    claude -p \
      --permission-mode bypassPermissions \
      --max-turns 50 \
      "$(cat scripts/github/ai_assess/batch_assess_issues_prompt.md)" \
      >> var/batch_assess_issues.log 2>&1 || true

    echo "$(date): Batch $batch_count complete" >> var/batch_assess_issues.log
done
