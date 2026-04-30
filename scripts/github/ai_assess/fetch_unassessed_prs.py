#!/usr/bin/env python3
"""Fetch the next batch of unassessed PRs with contributor context.

Outputs ALL_ASSESSED sentinel if no PRs remain, otherwise outputs
contributor context followed by the PR batch data formatted for
prompt injection.

Usage:
    PYTHONPATH=src python scripts/github/ai_assess/fetch_unassessed_prs.py
"""
import asyncio
import sys
from pathlib import Path

# Add shared scripts to path for fetch_contributor_context import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from sqlalchemy import select

from project_lens.database import async_session
from project_lens.models import GithubPR, PRAssessment
from fetch_contributor_context import get_contributor_context


async def main():
    async with async_session() as s:
        # Map (repo, pr_number) -> assessment updated_at timestamp
        assessed = await s.execute(
            select(PRAssessment.repo, PRAssessment.pr_number, PRAssessment.updated_at)
        )
        assessment_map = {(r[0], r[1]): r[2] for r in assessed.all()}

        result = await s.execute(
            select(GithubPR)
            .where(GithubPR.detail_fetched_at.is_not(None))
            .order_by(GithubPR.repo, GithubPR.number.desc())
        )

        prs = []
        for pr in result.scalars().all():
            key = (pr.repo, pr.number)
            if key not in assessment_map:
                # Never assessed
                prs.append(pr)
            elif pr.state == "open" and pr.detail_fetched_at > assessment_map[key]:
                # Open PR with new activity since last assessment
                prs.append(pr)

    if not prs:
        print("ALL_ASSESSED")
        return

    batch = prs[:30]

    # Contributor context
    context = await get_contributor_context(include_merge_authorities=True)
    print(context)
    print()

    # PR batch data
    new_count = sum(1 for p in prs if (p.repo, p.number) not in assessment_map)
    stale_count = len(prs) - new_count
    print(f"PRs to assess: {len(prs)} ({new_count} new, {stale_count} updated)")
    print(f"Processing batch of {len(batch)}")
    print()

    for pr in batch:
        reviews = pr.reviews_json or []
        review_comments = pr.review_comments_json or []
        issue_comments = pr.issue_comments_json or []
        print(f"--- {pr.repo} PR #{pr.number}: {pr.title} ---")
        print(f"Author: {pr.author} | State: {pr.state} | Labels: {pr.labels}")
        if pr.merged_at_gh:
            print(f"Merged: {str(pr.merged_at_gh)[:10]}")
        if pr.closed_at_gh and not pr.merged_at_gh:
            print(f"Closed: {str(pr.closed_at_gh)[:10]}")
        print(f"Body: {(pr.body or '')[:2000]}")
        print(f"Reviews ({len(reviews)}):")
        for r in reviews:
            print(f"  [{r.get('submitted_at', '?')[:10]}] {r['user']['login']}: {r['state']} - {(r.get('body') or '')[:2000]}")
        print(f"Review comments ({len(review_comments)}):")
        for c in review_comments:
            print(f"  [{c.get('created_at', '?')[:10]}] {c['user']['login']}: {(c.get('body') or '')[:2000]}")
        print(f"Issue comments ({len(issue_comments)}):")
        for c in issue_comments:
            print(f"  [{c.get('created_at', '?')[:10]}] {c['user']['login']}: {(c.get('body') or '')[:2000]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
