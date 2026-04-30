#!/usr/bin/env python3
"""Delete assessments for open issues so the batch assessment job regenerates them.

Run this when the assessment prompt has changed and open issues need
fresh assessments with the new logic. Closed issue assessments
are left untouched.

Usage:
    python scripts/reassess_open_issues.py --dry-run    # show what would be deleted
    python scripts/reassess_open_issues.py               # delete and report
    python scripts/reassess_open_issues.py --limit N     # delete at most N (oldest assessment first)
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.database import engine, async_session
from project_lens.models import Base, IssueAssessment, GithubIssue, AnalysisRun


async def main():
    dry_run = "--dry-run" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        query = (
            select(IssueAssessment)
            .join(GithubIssue, (IssueAssessment.repo == GithubIssue.repo) & (IssueAssessment.issue_number == GithubIssue.number))
            .where(GithubIssue.state == "open")
            .order_by(IssueAssessment.updated_at.asc())
        )
        if limit:
            query = query.limit(limit)
        result = await session.execute(query)
        assessments = result.scalars().all()

        if not assessments:
            print("No open issue assessments found.")
            return

        print(f"Open issues with assessments: {len(assessments)}")
        for a in assessments:
            print(f"  {a.repo} #{a.issue_number} — tier:{a.tier} state:{a.discussion_state}")

        if dry_run:
            print(f"\n--dry-run: would delete {len(assessments)} assessments")
            return

        for a in assessments:
            await session.delete(a)

        session.add(AnalysisRun(
            run_type="reassess_open_issues",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="completed",
            summary=f"Deleted {len(assessments)} open issue assessments for reassessment.",
        ))

        await session.commit()
        print(f"\nDeleted {len(assessments)} assessments. Run batch_assess_issues to regenerate.")


if __name__ == "__main__":
    asyncio.run(main())
