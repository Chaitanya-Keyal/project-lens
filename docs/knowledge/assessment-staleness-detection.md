# Assessment Staleness Detection

## Problem

The AI assessment pipeline originally used a pure existence check to decide which PRs/issues to assess: if a row existed in `pr_assessments` or `issue_assessments`, the item was skipped. This meant assessments were never refreshed when new comments, reviews, or commits arrived on a PR/issue.

The GitHub data layer (`github_scan.py`) correctly re-fetched detail data (reviews, comments) whenever `updated_at_gh > detail_fetched_at`, but the assessment layer didn't notice the refresh.

## Solution

Compare `detail_fetched_at` (set by `github_scan.py` when it fetches fresh reviews/comments) against `assessment.updated_at` (set when the assessment is written via UPSERT). If `detail_fetched_at > updated_at`, the assessment is stale.

Restricted to **open** PRs/issues only — closed/merged items don't need reassessment.

## Why these timestamps work

The pipeline runs sequentially: scan first, then assess. After assessment, `updated_at` (set to NOW) is always later than `detail_fetched_at`. On the next cycle, if no new activity, `detail_fetched_at` stays the same — no reassessment. If new activity arrives, `github_scan.py` bumps `detail_fetched_at` to NOW, making it later than the assessment's `updated_at` — triggering reassessment.

No new database columns were needed.

## Files

- `scripts/github/ai_assess/fetch_unassessed_prs.py` — PR staleness check
- `scripts/github/ai_assess/fetch_unassessed_issues.py` — issue staleness check
- `scripts/github/ingest/github_scan.py` — maintains `detail_fetched_at` (unchanged)
- `scripts/github/ai_assess/write_assessment.py` — UPSERT sets `updated_at = NOW()` (unchanged)
