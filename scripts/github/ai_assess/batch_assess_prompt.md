You are running as an unattended batch job for the project-lens indexer. Your task is to generate PR assessments for PRs that don't have them yet.

## Prefetched data

Below under "Prefetched Data" you will find:
- **Contributor profiles** with tier scores (merge, shipped, review) and **merge authorities**
- **A batch of unassessed PRs** with their review data, review comments, and issue comments

Use the tier scores to weight reviews and assess merge readiness:
- Contributors with **high** merge tier are merge authorities — their ACK carries the most weight.
- Contributors with **high** or **moderate** review tier are experienced reviewers — their substantive review satisfies the "trusted review" prerequisite for merge readiness.
- Contributors with only **low** or **none** tiers are newer participants — their reviews are still valuable but don't alone satisfy merge readiness requirements.

## Generate assessments

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
- `merge_target`: GitHub login of a merge authority from the prefetched data, or `"blocked"`.
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
- merge_target should reflect which merge authority should handle based on their scope (from the Merge Authorities list in the prefetched data).

### Reviewer sentiment guidelines
- Characterize each reviewer's current stance in one word (supportive, skeptical, neutral, concerned, satisfied, redirecting, etc.)
- The "needs" field describes what the author should do to satisfy this reviewer.

### Stale reason guidelines
- **neglected**: No engagement despite being open a long time, or author went silent after feedback.
- **indecisive**: Active discussion but no convergence on direction.
- **soft_rejected**: Minimal engagement from maintainers on a PR from a contributor who isn't well-known.
- **null**: PR is actively progressing or recently active.

## Write assessments to DB

Write the batch as a JSON array to a temp file, then run:

```bash
PYTHONPATH=src DEBUG=false python scripts/github/ai_assess/write_assessment.py --file /tmp/pr_batch_assessments.json
```

The write script validates the JSON schema before writing. If it prints validation errors, read each error, fix the JSON file accordingly, and re-run the write script. Repeat until it succeeds.

Do NOT prefix commands with `source .venv/bin/activate` — the venv is already active.

## Report and exit

After writing, report what was processed and exit. The outer loop script will relaunch with a fresh context for the next batch.

```
Assessed N PRs in this batch.
```
