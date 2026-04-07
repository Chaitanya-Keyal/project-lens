#!/usr/bin/env python3
"""Fetch the next batch of unassessed GitHub issues with contributor context.

Outputs ALL_ASSESSED sentinel if no issues remain, otherwise outputs
contributor context followed by the issue batch data formatted for
prompt injection.

Usage:
    PYTHONPATH=src python scripts/github/ai_assess/fetch_unassessed_issues.py
"""
import asyncio
import sys
from pathlib import Path

# Add shared scripts to path for fetch_contributor_context import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from sqlalchemy import select

from project_lens.database import async_session
from project_lens.models import GithubIssue, IssueAssessment
from fetch_contributor_context import get_contributor_context


async def main():
    async with async_session() as s:
        assessed = await s.execute(select(IssueAssessment.repo, IssueAssessment.issue_number))
        assessed_set = {(r[0], r[1]) for r in assessed.all()}

        result = await s.execute(
            select(GithubIssue)
            .where(GithubIssue.detail_fetched_at.is_not(None))
            .order_by(GithubIssue.repo, GithubIssue.number.desc())
        )
        issues = [i for i in result.scalars().all() if (i.repo, i.number) not in assessed_set]

    if not issues:
        print("ALL_ASSESSED")
        return

    batch = issues[:30]

    # Contributor context
    context = await get_contributor_context(include_merge_authorities=True)
    print(context)
    print()

    # Issue batch data
    print(f"Unassessed issues remaining: {len(issues)}")
    print(f"Processing batch of {len(batch)}")
    print()

    for issue in batch:
        comments = issue.comments_json or []
        print(f"--- {issue.repo} Issue #{issue.number}: {issue.title} ---")
        print(f"Author: {issue.author} | State: {issue.state} | Labels: {issue.labels}")
        print(f"Created: {str(issue.created_at_gh)[:10]}")
        if issue.closed_at_gh:
            print(f"Closed: {str(issue.closed_at_gh)[:10]}")
        print(f"Body: {(issue.body or '')[:500]}")
        print(f"Comments ({len(comments)}):")
        for c in comments:
            print(f"  [{c.get('created_at', '?')[:10]}] {c['user']['login']} ({c.get('author_association', '?')}): {(c.get('body') or '')[:300]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
