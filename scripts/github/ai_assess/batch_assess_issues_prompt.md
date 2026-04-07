You are running as an unattended batch job for the project-lens indexer. Your task is to generate issue assessments for GitHub issues that don't have them yet.

## Setup
Working directory: /home/kdmukai/dev/tools/project-lens
Python venv: source .venv/bin/activate

## Step 0: Load contributor and project context

Run this to get contributor profiles and merge authorities from the database. Use this context when assessing whether comments carry weight.

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

Use the tier scores to weight comments:
- Contributors with **high** merge tier are core maintainers — their statements on an issue carry the most weight.
- Contributors with **high** or **moderate** review/PR tier are established contributors — their technical opinions are credible.
- Other participants may raise valid points, but their comments alone don't constitute project direction.

## Step 1: Find unassessed issues

Run this to get the next batch of unassessed issues with their comment data:

```bash
source .venv/bin/activate && PYTHONPATH=src DEBUG=false python -c "
import asyncio, json
from project_lens.database import async_session
from project_lens.models import GithubIssue, IssueAssessment
from sqlalchemy import select

async def dump():
    async with async_session() as s:
        assessed = await s.execute(select(IssueAssessment.repo, IssueAssessment.issue_number))
        assessed_set = {(r[0], r[1]) for r in assessed.all()}

        result = await s.execute(
            select(GithubIssue)
            .where(GithubIssue.detail_fetched_at.is_not(None))
            .order_by(GithubIssue.repo, GithubIssue.number.desc())
        )
        issues = [i for i in result.scalars().all() if (i.repo, i.number) not in assessed_set]

        # Take next batch of 30
        batch = issues[:30]
        print(f'Unassessed issues remaining: {len(issues)}')
        print(f'Processing batch of {len(batch)}')
        print()

        for issue in batch:
            comments = issue.comments_json or []
            print(f'--- {issue.repo} Issue #{issue.number}: {issue.title} ---')
            print(f'Author: {issue.author} | State: {issue.state} | Labels: {issue.labels}')
            print(f'Created: {str(issue.created_at_gh)[:10]}')
            if issue.closed_at_gh: print(f'Closed: {str(issue.closed_at_gh)[:10]}')
            print(f'Body: {(issue.body or \"\")[:500]}')
            print(f'Comments ({len(comments)}):')
            for c in comments:
                print(f'  [{c.get(\"created_at\",\"?\")[:10]}] {c[\"user\"][\"login\"]} ({c.get(\"author_association\", \"?\")}): {(c.get(\"body\") or \"\")[:300]}')
            print()

asyncio.run(dump())
"
```

## Step 2: Generate assessments

For each issue, fill in this exact JSON skeleton. Do not add, remove, or rename any fields.

```json
{
  "repo": "Owner/RepoName",
  "issue_number": 0,
  "tier": 0,
  "category": "bug|feature_request|question|discussion|task|support",
  "complexity": "trivial|low|medium|high",
  "impact": "low|medium|high",
  "summary": "Narrative of what the issue is about and what happened in the discussion.",
  "recommendation": "What should happen next, or retrospective assessment if closed.",
  "discussion_state": "active|stale|resolved|wont_fix|duplicate",
  "stale_reason": "neglected|no_traction|waiting_on_author|waiting_on_maintainer|null",
  "cross_references": [
    {"type": "related_pr|fixed_by|duplicate_of|discussed_in|depends_on|spawned", "target": "Owner/Repo#123", "note": "context"}
  ],
  "participant_contributions": [
    {
      "github_username": "login",
      "role": "reporter|maintainer|contributor|commenter",
      "contributions": [
        {"type": "contribution_type", "detail": "What they did"}
      ]
    }
  ],
  "discussion_positions": [
    {"user": "login", "position": "Brief summary of their stance or key contribution to the discussion"}
  ],
  "labels_suggested": ["bug", "enhancement"]
}
```

### Contribution types
bug_report, feature_proposal, design_suggestion, technical_analysis, use_case, workaround,
reproduction_steps, testing_feedback, priority_argument, security_concern, ux_feedback,
implementation_guidance, scope_definition, counterargument, consensus_building, triage

### Schema rules — the write script will reject output that violates these:
- `tier`: integer 0-3.
- `category`: one of `bug`, `feature_request`, `question`, `discussion`, `task`, `support`.
- `complexity`: one of `trivial`, `low`, `medium`, `high`.
- `impact`: one of `low`, `medium`, `high`.
- `discussion_state`: one of `active`, `stale`, `resolved`, `wont_fix`, `duplicate`.
- `stale_reason`: one of `neglected`, `no_traction`, `waiting_on_author`, `waiting_on_maintainer`, or `null`. Required when discussion_state is `stale`, must be `null` otherwise.
- `cross_references`: array of objects with `type`, `target`, `note`. Type must be one of `related_pr`, `fixed_by`, `duplicate_of`, `discussed_in`, `depends_on`, `spawned`.
- `discussion_positions`: array of objects with `user` (string) and `position` (string).
- `labels_suggested`: array of strings.
- Do not invent new fields or change the shape.

### Guidelines

**What issue assessment is about:**

Unlike PR assessments (which track toward a merge decision), issue assessments capture the state of a conversation that may or may not have reached any conclusion. The value is in:
- Summarizing what was discussed and by whom
- Noting whether anyone with project authority weighed in
- Honestly characterizing when a discussion fizzled out or went nowhere
- Identifying actionable next steps when they exist, and acknowledging when they don't

**Do not force conclusions.** Many issues have no resolution — the discussion petered out, nobody with authority made a call, or the issue is simply a parking lot for an idea. Say so plainly. The assessment should reflect reality, not impose a narrative of progress.

- **Tier 0** = trivial (typo reports, simple questions with quick answers)
- **Tier 1** = straightforward (clear bug reports, simple feature requests with minimal discussion)
- **Tier 2** = significant (feature proposals with substantive discussion, bugs with complex reproduction)
- **Tier 3** = major (architectural proposals, security issues, contentious design decisions)

**Discussion state:**
- **active**: Recent activity or clear next steps being pursued.
- **stale**: No meaningful activity for an extended period relative to the issue's nature. A 6-month-old feature brainstorm is less stale than a 2-month-old bug with no response.
- **resolved**: The issue was addressed — either explicitly closed with a fix/answer, or the underlying problem is clearly solved even if the issue wasn't formally closed.
- **wont_fix**: Explicitly or implicitly rejected by maintainers.
- **duplicate**: Duplicate of another issue.

**Stale reason (only when discussion_state is stale):**
- **neglected**: Issue was filed and never got meaningful engagement from maintainers.
- **no_traction**: Some discussion happened but it didn't lead anywhere — no decisions, no follow-through.
- **waiting_on_author**: Maintainer asked for more info or changes and author went silent.
- **waiting_on_maintainer**: Author provided what was asked but maintainer hasn't responded.

**For closed issues:** Assess what actually happened. Was it resolved by a PR? Closed as wont-fix? Went stale and got swept up in a cleanup? The discussion_state should reflect the real outcome, not just the GitHub state.

**For issues with no comments:** Keep it brief. Summarize the issue, note the lack of engagement, and move on. Don't fabricate analysis where there's nothing to analyze.

**Participant roles:**
- **reporter**: The person who opened the issue.
- **maintainer**: Core contributors (high merge tier) commenting on the issue.
- **contributor**: Established contributors (moderate+ tiers) participating.
- **commenter**: Everyone else.

**Cross-references:** Extract explicit links to PRs, other issues, or discussions mentioned in the issue body or comments. Also note implicit connections (e.g., a PR that clearly addresses this issue even if not explicitly linked).

**labels_suggested:** Suggest what labels should be on this issue based on its content. Use common GitHub label conventions (bug, enhancement, question, documentation, good first issue, etc.). This is a recommendation, not a reflection of current labels.

## Step 3: Write assessments to DB

Write the batch as a JSON array to a temp file, then run:

```bash
source .venv/bin/activate && PYTHONPATH=src DEBUG=false python scripts/github/ai_assess/write_issue_assessment.py --file /tmp/issue_batch_assessments.json
```

The write script validates the JSON schema before writing. If it prints validation errors, read each error, fix the JSON file accordingly, and re-run the write script. Repeat until it succeeds.

## Step 4: Report and exit

After writing, report what was processed and exit. The outer loop script will relaunch with a fresh context for the next batch.

```
Assessed N issues in this batch.
```
