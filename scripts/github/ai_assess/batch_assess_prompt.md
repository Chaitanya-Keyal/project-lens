You are running as an unattended batch job for the project-lens indexer. Your task is to generate PR assessments for PRs that don't have them yet.

## Setup
Working directory: /home/kdmukai/dev/tools/project-lens
Python venv: source .venv/bin/activate

## Step 0: Load contributor and project context

Run this to get contributor profiles and merge authorities from the database. Use this context when assessing reviews, merge readiness, and participant contributions.

```bash
source .venv/bin/activate && PYTHONPATH=src DEBUG=false python -c "
import asyncio, json
from project_lens.database import async_session
from project_lens.models import ContributorProfile, ProjectConfigEntry
from sqlalchemy import select

async def show():
    async with async_session() as s:
        # Contributor profiles
        result = await s.execute(
            select(ContributorProfile)
            .order_by(ContributorProfile.last_active.desc())
        )
        print('=== Contributor Profiles ===')
        for p in result.scalars():
            s_ = p.stats
            total = s_.get('merge_commits',0) + s_.get('prs_merged',0) + s_.get('total_review_engagement',0)
            if total < 5:
                continue
            mt = s_.get('merge_tier', 'none')
            pt = s_.get('pr_tier', 'none')
            rt = s_.get('review_tier', 'none')
            print(f'{p.github_login} — merges:{mt} shipped:{pt} reviews:{rt} — {s_.get(\"merge_commits\",0)} merges, {s_.get(\"prs_merged\",0)} PRs merged, {s_.get(\"total_review_engagement\",0)} review engagements')

        # Merge authorities from project config
        result = await s.execute(
            select(ProjectConfigEntry).where(ProjectConfigEntry.key == 'merge_authorities')
        )
        entry = result.scalar_one_or_none()
        if entry:
            print()
            print('=== Merge Authorities ===')
            for ma in entry.value:
                print(f'{ma[\"login\"]} — scope: {ma[\"scope\"]} — {ma.get(\"description\", \"\")}')

asyncio.run(show())
"
```

Use the tier scores to weight reviews and assess merge readiness:
- Contributors with **high** merge tier are merge authorities — their ACK carries the most weight.
- Contributors with **high** or **moderate** review tier are experienced reviewers — their substantive review satisfies the "trusted review" prerequisite for merge readiness.
- Contributors with only **low** or **none** tiers are newer participants — their reviews are still valuable but don't alone satisfy merge readiness requirements.

## Step 1: Find unassessed PRs
Run this to get the next batch of unassessed PRs with their review data:

```bash
source .venv/bin/activate && PYTHONPATH=src DEBUG=false python -c "
import asyncio, json
from project_lens.database import async_session
from project_lens.models import GithubPR, PRAssessment
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
            print(f'--- {pr.repo} PR #{pr.number}: {pr.title} ---')
            print(f'Author: {pr.author} | State: {pr.state} | Labels: {pr.labels}')
            if pr.merged_at_gh: print(f'Merged: {str(pr.merged_at_gh)[:10]}')
            if pr.closed_at_gh and not pr.merged_at_gh: print(f'Closed: {str(pr.closed_at_gh)[:10]}')
            print(f'Body: {(pr.body or \"\")[:400]}')
            print(f'Reviews ({len(reviews)}):')
            for r in reviews:
                print(f'  [{r.get(\"submitted_at\",\"?\")[:10]}] {r[\"user\"][\"login\"]}: {r[\"state\"]} - {(r.get(\"body\") or \"\")[:200]}')
            print(f'Review comments ({len(review_comments)}):')
            for c in review_comments:
                print(f'  [{c.get(\"created_at\",\"?\")[:10]}] {c[\"user\"][\"login\"]}: {(c.get(\"body\") or \"\")[:200]}')
            print(f'Issue comments ({len(issue_comments)}):')
            for c in issue_comments:
                print(f'  [{c.get(\"created_at\",\"?\")[:10]}] {c[\"user\"][\"login\"]}: {(c.get(\"body\") or \"\")[:200]}')

            print()

asyncio.run(dump())
"
```

## Step 2: Generate assessments

For each PR, fill in this exact JSON skeleton. Do not add, remove, or rename any fields.

For **closed/merged PRs**, omit `pending_actions`, `merge_readiness`, `merge_readiness_rationale`, `reviewer_sentiment`, and `stale_reason` entirely.

```json
{
  "repo": "Owner/RepoName",
  "pr_number": 0,
  "tier": 0,
  "merge_target": "github_login or blocked",
  "tested_on_hardware": false,
  "complexity": "trivial|low|medium|high",
  "risk": "low|medium|high",
  "summary": "Narrative of what the PR does and what happened during review.",
  "recommendation": "Assessment of the PR's significance.",
  "outcome": {
    "category": "feature|bugfix|refactor|cleanup|security|docs|l10n|performance|testing",
    "impact": "What changed for users or the codebase",
    "review_improved_outcome": false,
    "review_improvement": "How review made the result better, or null"
  },
  "cross_references": [
    {"type": "spawned|depends_on|fixes|related_to|discussed_in", "target": "Owner/Repo#123", "note": "context"}
  ],
  "participant_contributions": [
    {
      "github_username": "login",
      "role": "author|reviewer_primary|reviewer_secondary|reviewer",
      "contributions": [
        {"type": "contribution_type", "detail": "What they did"}
      ]
    }
  ],
  "lessons_learned": [
    {
      "category": "architecture|process|code_quality|testing|security|l10n|performance",
      "principle": "The reusable takeaway",
      "detail": "Specific context from this PR",
      "source": "Who articulated it"
    }
  ],
  "reviews": [
    {"user": "login", "type": "APPROVED|COMMENTED|CHANGES_REQUESTED"}
  ],
  "pending_actions": {
    "username": {
      "role": "author|reviewer",
      "open_items": 0,
      "summary": "username needs to do X (must start with their username)",
      "last_activity_at": "YYYY-MM-DD"
    }
  },
  "merge_readiness": "not_ready|possibly_ready|merge_ready",
  "merge_readiness_rationale": "Brief explanation of the classification",
  "reviewer_sentiment": [
    {"reviewer": "login", "sentiment": "one_word", "needs": "What author should do"}
  ],
  "stale_reason": "neglected|indecisive|soft_rejected|null"
}
```

### Contribution types
architectural_guidance, bug_catch, code_suggestion, tested_ack, ack, rubber_stamp_ack,
edge_case_found, naming_improvement, test_coverage_request, mentoring, process_feedback,
hardware_tested, security_review, feature_implementation, responsive_to_feedback, performance_improvement

### Schema rules — the write script will reject output that violates these:
- `tier`: integer 0-3.
- `complexity`: one of `trivial`, `low`, `medium`, `high`.
- `risk`: one of `low`, `medium`, `high`.
- `merge_target`: GitHub login of a merge authority from Step 0, or `"blocked"`.
- `merge_readiness`: one of `not_ready`, `possibly_ready`, `merge_ready`.
- `stale_reason`: one of `neglected`, `indecisive`, `soft_rejected`, or `null`.
- `reviewer_sentiment`: array of objects with `reviewer` (string), `sentiment` (single word, no spaces), `needs` (string).
- `pending_actions`: dict keyed by GitHub username, each with `role` (string), `open_items` (integer), `summary` (string starting with the username), `last_activity_at` (YYYY-MM-DD string).
- `reviews[].type`: one of `APPROVED`, `COMMENTED`, `CHANGES_REQUESTED`.
- `outcome.category`: one of `feature`, `bugfix`, `refactor`, `cleanup`, `security`, `docs`, `l10n`, `performance`, `testing`.
- Do not invent new fields or change the shape.

### Guidelines
- Deduplicate participant contributions by substance — if the same point is made across multiple comments, describe it once. Preserve distinct contributions even if they share the same type.
- For trivial PRs (version bumps, typo fixes, quick ACKs), keep it brief — minimal participant detail, empty lessons_learned.
- For substantive PRs with review discussion, extract the full richness: what each reviewer contributed, what lessons emerged, whether review improved the outcome.
- Tier 0 = trivial, Tier 1 = straightforward, Tier 2 = significant, Tier 3 = complex/high-impact.
- cross_references: extract explicit links to other PRs, issues, or discussions mentioned in PR body or comments.
- lessons_learned: only include when genuine design principles or process lessons emerged from the review. Many PRs have none — that's fine.

### Open PRs vs closed/merged PRs
The following fields are **only for open PRs** — omit them entirely for closed/merged PRs:
- `pending_actions`, `merge_readiness`, `merge_readiness_rationale`, `reviewer_sentiment`, `stale_reason`

Closed/merged PRs only need the core assessment fields (tier, complexity, risk, summary, recommendation, outcome, participant_contributions, lessons_learned, cross_references, reviews).

### Pending actions guidelines
- Each person involved in the PR gets an entry listing only what's on THEIR plate.
- open_items is approximate — count substantive unaddressed comments/requests, not nits.
- A commit that addresses feedback counts even without explicit replies to each comment.
- For PRs with no reviewer engagement: author has 0 items, summary says "No reviewers have engaged yet."
- For closed/merged PRs: all participants have 0 items (nothing pending).
- **summary must name the person by their GitHub username explicitly.** The downstream consumer displays each person's summary in a personalized context where "the author" or an omitted subject is ambiguous. Always write the summary so it reads clearly standalone.
  - Good: "kdmukai needs to address 2 review comments from newtonick"
  - Bad: "needs to address 2 review comments" (who?)
  - Bad: "the author needs to address 2 review comments" (impersonal)

### Merge readiness guidelines
- **not_ready**: No ACK from a reviewer with high or moderate review tier on a non-trivial PR, or substantial unresolved review threads. Most PRs are not_ready — that's normal.
- **possibly_ready**: Has substantive ACK(s) from reviewer(s) with high/moderate review tier, but the PR's complexity or risk warrants review from a merge authority (high merge tier). The prerequisite is at least one substantive review from an established reviewer — an ACK from a low/none-tier contributor or a drive-by LGTM doesn't qualify.
- **merge_ready**: ACKs from established reviewers, CI presumably passing, review threads resolved. Conservative threshold — if there's doubt, use possibly_ready.
- merge_target should reflect which merge authority should handle based on their scope (from the Merge Authorities list in Step 0).

### Reviewer sentiment guidelines
- Characterize each reviewer's current stance in one word (supportive, skeptical, neutral, concerned, satisfied, redirecting, etc.)
- The "needs" field describes what the author should do to satisfy this reviewer.

### Stale reason guidelines
- **neglected**: No engagement despite being open a long time, or author went silent after feedback.
- **indecisive**: Active discussion but no convergence on direction.
- **soft_rejected**: Minimal engagement from maintainers on a PR from a contributor who isn't well-known.
- **null**: PR is actively progressing or recently active.

## Step 3: Write assessments to DB

Write the batch as a JSON array to a temp file, then run:

```bash
source .venv/bin/activate && PYTHONPATH=src DEBUG=false python scripts/github/ai_assess/write_assessment.py --file /tmp/pr_batch_assessments.json
```

The write script validates the JSON schema before writing. If it prints validation errors, read each error, fix the JSON file accordingly, and re-run the write script. Repeat until it succeeds.

## Step 4: Report and exit

After writing, report what was processed and exit. The outer loop script will relaunch with a fresh context for the next batch.

```
Assessed N PRs in this batch.
```
