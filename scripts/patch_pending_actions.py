#!/usr/bin/env python3
"""Patch only the pending_actions field on existing PR assessments.

Used by the one-shot rewrite prompt to update summaries without touching
the rest of the assessment.

Usage:
    python scripts/patch_pending_actions.py --file /tmp/patched.json

Input JSON: array of {"repo": "...", "pr_number": 123, "pending_actions": {...}}
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine, async_session
from backend.models import Base, PRAssessment


async def main():
    if "--file" not in sys.argv:
        print("Usage: patch_pending_actions.py --file <path>")
        sys.exit(1)

    idx = sys.argv.index("--file")
    with open(sys.argv[idx + 1]) as f:
        patches = json.load(f)

    if isinstance(patches, dict):
        patches = [patches]

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    updated = 0

    for patch in patches:
        repo = patch["repo"]
        pr_number = patch["pr_number"]
        pending_actions = patch["pending_actions"]

        async with async_session() as session:
            result = await session.execute(
                select(PRAssessment).where(
                    PRAssessment.repo == repo,
                    PRAssessment.pr_number == pr_number,
                )
            )
            assessment = result.scalar_one_or_none()
            if not assessment:
                print(f"  PR #{pr_number} ({repo}): not found, skipping")
                continue

            assessment.pending_actions = pending_actions
            assessment.pending_actions_updated_at = now
            await session.commit()
            updated += 1
            print(f"  PR #{pr_number} ({repo}): patched")

    print(f"\nDone: {updated}/{len(patches)} assessment(s) patched.")


if __name__ == "__main__":
    asyncio.run(main())
