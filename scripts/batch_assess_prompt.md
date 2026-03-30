You are running as an unattended batch job for the project-lens indexer. Your task is to generate PR assessments for SeedSigner PRs that don't have them yet.

## Setup
Working directory: /home/kdmukai/dev/tools/project-lens
Python venv: source .venv/bin/activate

## Step 1: Find unassessed PRs
Run this to get the next batch of unassessed PRs with their review data:

```bash
source .venv/bin/activate && DEBUG=false python -c "
import asyncio, json
from backend.database import async_session
from backend.models import GithubPR, PRAssessment
from sqlalchemy import select

async def dump():
    async with async_session() as s:
        assessed = await s.execute(select(PRAssessment.repo, PRAssessment.pr_number))
        assessed_set = {(r[0], r[1]) for r in assessed.all()}

        result = await s.execute(
            select(GithubPR)
            .where(GithubPR.detail_fetched_at.is_not(None))
            .order_by(GithubPR.repo, GithubPR.number.desc())
        )
        prs = [p for p in result.scalars().all() if (p.repo, p.number) not in assessed_set]

        # Take next batch of 30
        batch = prs[:30]
        print(f'Unassessed PRs remaining: {len(prs)}')
        print(f'Processing batch of {len(batch)}')
        print()

        for pr in batch:
            reviews = pr.reviews_json or []
            review_comments = pr.review_comments_json or []
            issue_comments = pr.issue_comments_json or []
            print(f'--- PR #{pr.number}: {pr.title} ---')
            print(f'Author: {pr.author} | State: {pr.state} | Labels: {pr.labels}')
            if pr.merged_at_gh: print(f'Merged: {str(pr.merged_at_gh)[:10]}')
            if pr.closed_at_gh and not pr.merged_at_gh: print(f'Closed: {str(pr.closed_at_gh)[:10]}')
            print(f'Body: {(pr.body or \"\")[:400]}')
            print(f'Reviews ({len(reviews)}):')
            for r in reviews[:10]:
                print(f'  {r[\"user\"][\"login\"]}: {r[\"state\"]} - {(r.get(\"body\") or \"\")[:150]}')
            if len(reviews) > 10: print(f'  ... and {len(reviews)-10} more')
            print(f'Review comments ({len(review_comments)}):')
            for c in review_comments[:8]:
                print(f'  {c[\"user\"][\"login\"]}: {(c.get(\"body\") or \"\")[:150]}')
            if len(review_comments) > 8: print(f'  ... and {len(review_comments)-8} more')
            print(f'Issue comments ({len(issue_comments)}):')
            for c in issue_comments[:8]:
                print(f'  {c[\"user\"][\"login\"]}: {(c.get(\"body\") or \"\")[:150]}')
            if len(issue_comments) > 8: print(f'  ... and {len(issue_comments)-8} more')
            print()

asyncio.run(dump())
"
```

## Step 2: Generate assessments

For each PR, produce a JSON assessment with this structure:

```json
{
  "repo": "SeedSigner/seedsigner",
  "pr_number": 123,
  "tier": 0-3,
  "merge_target": "nick|seedsigner-account|blocked|other",
  "tested_on_hardware": true/false,
  "complexity": "trivial|low|medium|high",
  "risk": "low|medium|high",
  "summary": "Brief narrative of what the PR does and what happened during review.",
  "recommendation": "Assessment of the PR's significance and any notable aspects.",
  "outcome": {
    "category": "feature|bugfix|refactor|cleanup|security|docs|l10n|performance|testing",
    "impact": "What changed for users or the codebase",
    "review_improved_outcome": true/false,
    "review_improvement": "How review made the result better (or null)"
  },
  "cross_references": [
    {"type": "spawned|depends_on|fixes|related_to|discussed_in", "target": "...", "note": "..."}
  ],
  "participant_contributions": [
    {
      "github_username": "...",
      "role": "author|reviewer_primary|reviewer_secondary|reviewer",
      "contributions": [
        {"type": "...", "detail": "..."}
      ]
    }
  ],
  "lessons_learned": [
    {"category": "architecture|process|code_quality|testing|security|l10n|performance",
     "principle": "The reusable takeaway",
     "detail": "Specific context from this PR",
     "source": "Who articulated it"}
  ],
  "reviews": [{"user": "...", "type": "APPROVED|COMMENTED|CHANGES_REQUESTED"}]
}
```

### Contribution types
architectural_guidance, bug_catch, code_suggestion, tested_ack, ack, rubber_stamp_ack,
edge_case_found, naming_improvement, test_coverage_request, mentoring, process_feedback,
hardware_tested, security_review, feature_implementation, responsive_to_feedback, performance_improvement

### Guidelines
- Deduplicate participant contributions by substance — if the same point is made across multiple comments, describe it once. Preserve distinct contributions even if they share the same type.
- For trivial PRs (version bumps, typo fixes, quick ACKs), keep it brief — minimal participant detail, empty lessons_learned.
- For substantive PRs with review discussion, extract the full richness: what each reviewer contributed, what lessons emerged, whether review improved the outcome.
- Tier 0 = trivial, Tier 1 = straightforward, Tier 2 = significant, Tier 3 = complex/high-impact.
- cross_references: extract explicit links to other PRs, issues, or discussions mentioned in PR body or comments.
- lessons_learned: only include when genuine design principles or process lessons emerged from the review. Many PRs have none — that's fine.

## Step 3: Write assessments to DB

Write the batch as a JSON array to a temp file, then run:

```bash
source .venv/bin/activate && DEBUG=false python scripts/write_assessment.py --file /tmp/pr_batch_assessments.json
```

## Step 4: Report progress

After writing, run:

```bash
source .venv/bin/activate && DEBUG=false python -c "
import asyncio
from backend.database import async_session
from backend.models import PRAssessment, GithubPR
from sqlalchemy import select, func

async def check():
    async with async_session() as s:
        r = await s.execute(select(func.count(PRAssessment.id)))
        assessed = r.scalar()
        r = await s.execute(select(func.count(GithubPR.id)))
        total = r.scalar()
        print(f'Assessments: {assessed}/{total} PRs')

asyncio.run(check())
"
```

Repeat steps 1-4 until all PRs are assessed or you run out of context.
