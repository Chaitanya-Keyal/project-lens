#!/usr/bin/env python3
"""Delete assessments for open PRs so the batch assessment job regenerates them.

Run this when the assessment prompt has changed and open PRs need
fresh assessments with the new logic. Closed/merged PR assessments
are left untouched — their merge_target can be fixed programmatically
from git history.

Usage:
    python scripts/reassess_open_prs.py --dry-run    # show what would be deleted
    python scripts/reassess_open_prs.py               # delete and report
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine, async_session
from backend.models import Base, PRAssessment, GithubPR, AnalysisRun


async def main():
    dry_run = "--dry-run" in sys.argv

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # Find open PRs with existing assessments
        result = await session.execute(
            select(PRAssessment)
            .join(GithubPR, (PRAssessment.repo == GithubPR.repo) & (PRAssessment.pr_number == GithubPR.number))
            .where(GithubPR.state == "open")
        )
        assessments = result.scalars().all()

        if not assessments:
            print("No open PR assessments found.")
            return

        print(f"Open PRs with assessments: {len(assessments)}")
        for a in assessments:
            print(f"  {a.repo} #{a.pr_number} — tier:{a.tier} readiness:{a.merge_readiness}")

        if dry_run:
            print(f"\n--dry-run: would delete {len(assessments)} assessments")
            return

        # Delete them
        for a in assessments:
            await session.delete(a)

        # Record the run
        session.add(AnalysisRun(
            run_type="reassess_open_prs",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=f"Deleted {len(assessments)} open PR assessments for reassessment.",
        ))

        await session.commit()
        print(f"\nDeleted {len(assessments)} assessments. Run batch_assess to regenerate.")


if __name__ == "__main__":
    asyncio.run(main())
