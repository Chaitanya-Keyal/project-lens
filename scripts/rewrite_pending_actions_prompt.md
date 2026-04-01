You are running as a one-shot batch job for project-lens. Your task is to rewrite the `pending_actions` summary fields on open PR assessments so that every summary explicitly names the person by their GitHub username.

## Why

The downstream consumer (Project PM) displays each person's summary in a personalized "Your Action Items" box. If the summary says "the author" or starts with a bare verb like "Needs to address...", it's ambiguous when shown to that person. Each summary must read clearly as a standalone sentence with the person named.

## Setup
Working directory: /home/kdmukai/dev/tools/project-lens
Python venv: source .venv/bin/activate

## Step 1: Dump current summaries

```bash
source .venv/bin/activate && DEBUG=false python -c "
import asyncio, json
from backend.database import async_session
from backend.models import PRAssessment, GithubPR
from sqlalchemy import select

async def dump():
    async with async_session() as s:
        result = await s.execute(
            select(PRAssessment)
            .join(GithubPR, (PRAssessment.repo == GithubPR.repo) & (PRAssessment.pr_number == GithubPR.number))
            .where(GithubPR.state == 'open')
            .where(PRAssessment.pending_actions.is_not(None))
        )
        assessments = result.scalars().all()

        all_open = []
        for a in assessments:
            pa = a.pending_actions
            if not pa:
                continue
            all_open.append({
                'repo': a.repo,
                'pr_number': a.pr_number,
                'pending_actions': pa,
            })

        print(json.dumps(all_open, indent=2))

asyncio.run(dump())
"
```

## Step 2: Rewrite summaries

For each entry, rewrite ONLY the `summary` field so that:

1. **The summary starts with the person's GitHub username.** For example:
   - Before: `"Needs to address kdmukai's Sep 3 comments..."` → After: `"abhigit-saha needs to address kdmukai's Sep 3 comments..."`
   - Before: `"Author rebased to current dev..."` → After: `"Chaitanya-Keyal rebased to current dev..."`
   - Before: `"Provided the direction change on Oct 15."` → After: `"kdmukai provided the direction change on Oct 15."`

2. **Replace "Author" or "author" mid-sentence with the actual username** when it refers to the PR author. Use context to determine who the author is (the person with `"role": "author"` in the same pending_actions dict).

3. **"No reviewers have engaged yet."** — Rewrite as: `"{username} has no pending items — no reviewers have engaged yet."`

4. **Do NOT change** the `role`, `open_items`, or `last_activity_at` fields. Only change `summary`.

5. **Do NOT change the substance** of the summary. This is a phrasing pass, not a reassessment.

If a summary already meets these criteria, leave it unchanged.

Produce the output as a JSON array matching the input structure (repo, pr_number, pending_actions) with updated summaries. Include ALL entries from the input — even ones you didn't change — so the patch script can apply them atomically.

## Step 3: Write to a temp file and apply

Write the JSON array to `/tmp/patched_pending_actions.json`, then run:

```bash
source .venv/bin/activate && DEBUG=false python scripts/patch_pending_actions.py --file /tmp/patched_pending_actions.json
```

## Step 4: Verify

Spot-check a few entries to confirm all summaries now start with the GitHub username.
