# PR State Metadata in ChromaDB

## The Bug
The `state` metadata field in the `pr_assessments` ChromaDB collection was misnamed. It stored `PRAssessment.merge_readiness` (an AI opinion: `not_ready`, `possibly_ready`, `merge_ready`) rather than the actual GitHub PR state (`open`, `closed`, `merged`).

This happened because `embed_digests.py` only queried the `pr_assessments` table — it never JOINed with `github_prs` where the real state lives.

## The Fix (commit 3602382)
- `fetch_pr_assessment_texts()` now JOINs `PRAssessment` with `GithubPR` via `outerjoin` on `(repo, pr_number) = (repo, number)`
- Metadata field renamed: `"state"` → `"merge_readiness"` (what it actually is)
- New metadata field added: `"github_state"` from `GithubPR.state`

## merge_readiness Lifecycle
- Only populated for PRs assessed while open (the AI prompt omits it for closed/merged PRs)
- If a PR is assessed while open, then later merges/closes, the `merge_readiness` value is preserved — `reassess_open_prs.py` only targets still-open PRs
- If a PR is first assessed after closing (e.g., historical backfill), `merge_readiness` will be `None`
- The upsert in `write_assessment.py` overwrites all fields, so a re-assessment of a closed PR would set `merge_readiness` to `None`

## Consumers
- Project PM's `search/_results.html` reads `r.metadata.github_state` to show Open/Merged/Closed badges
- `merge_readiness` is available but not currently displayed in search results
