# Assessment Input Fidelity — Truncation and Multi-Point Comments

## Problem

AI assessments of PRs and issues were systematically dropping substantive content. Two independent causes, easy to confuse because they produce similar symptoms (shallow summaries, missing review asks, reviewer_sentiment.needs that only captured the first point raised).

### Cause 1: input truncation in fetchers

`scripts/github/ai_assess/fetch_unassessed_prs.py` truncated every comment body to 200 characters, reviews to 200 characters, and PR bodies to 400 characters. `scripts/github/ai_assess/fetch_unassessed_issues.py` used 300 for comments and 500 for issue bodies.

A 200-character cap cuts off well before the end of most real review comments — a ~600-char comment with two points separated by a horizontal divider loses the second point entirely before the model even sees it. The model can't reason about content that never reached the prompt.

### Cause 2: prompt guidance didn't address multi-point comments

`batch_assess_prompt.md` had a "deduplicate by substance" rule for *across* comments, but nothing told the model that a *single* comment can carry multiple distinct contributions. Reviewers frequently write comments structured as:

- A nit or ask
- A horizontal divider (`---`)
- A separate architectural question or reference

Without explicit guidance, the model collapsed these into one `contribution` entry and a single `reviewer_sentiment.needs` string, typically capturing only the first point.

## Fix

### Fetchers — raise truncation cap

Body, review, and comment caps raised to **2000 chars** in both:
- `scripts/github/ai_assess/fetch_unassessed_prs.py`
- `scripts/github/ai_assess/fetch_unassessed_issues.py`

2000 chars covers ~95%+ of real review content without bloating batches. If a comment is genuinely longer than 2000 chars, it's rare enough that we can accept partial coverage.

### Prompts — explicit multi-point rule

Both `batch_assess_prompt.md` and `batch_assess_issues_prompt.md` now include a rule:

> A single comment can carry multiple distinct contributions. Split by topic shift, horizontal divider (`---`), numbered points, section headers, or explicit markers ("Also:", "Second,", "Separately:", "On another note:"). Record each distinct point as its own entry in `participant_contributions[].contributions` — do not collapse them into a single summary.

The PR prompt extends this to `reviewer_sentiment.needs` and `pending_actions.summary` ("should enumerate every open ask, not just the first"). The issues prompt extends it to `discussion_positions[].position`.

## Validation

Verified on three PRs (923, 909, 908) and three issues (555, 531, 349) with multi-point comments. Every reassessment captured additional distinct contributions that were previously collapsed or missing, without contradicting or degrading any prior entry.

Concrete examples:
- PR 923: kdmukai's comment went from 1 → 2 contributions; the second point (pointing to a #519 discussion and preferring a general solution) had been silently truncated off the input.
- PR 908: kdmukai's 2083-char comment had been collapsed to a generic "acknowledged the blind spot"; the actual `threading.excepthook` architectural proposal became visible after the fixes.
- Issue #349: kdmukai's comment had a separate security concern (idle-timeout wipe when seed is loaded) that was entirely absent from the previous assessment.

## Why these symptoms are easy to misdiagnose

The two causes produce the *same* user-visible symptom: a comment's later content is missing from the assessment. You can't distinguish "model saw it but dropped it" from "model never saw it" without printing the actual prompt input. When investigating similar "the assessment is shallow" reports, always verify the prompt text first — don't assume the model is the problem.

## Related — silent failure masking

`scripts/github/ai_assess/batch_assess.sh` and `batch_assess_issues.sh` swallow the inner `claude -p` exit code with `|| true` inside the while-loop. Any systematic failure (missing `claude` on PATH in cron context, rate-limit errors, API outages) produces no visible error and no assessment progress — the loop just spins, incrementing `batch_count` but doing no work.

If a batch run reports an implausibly large `batch_count` in one session ("after 9571 batches"), this is the signature. Grep the log for `command not found`, `rate`, `429`, or model refusal patterns before concluding anything about the pipeline's logical behavior.
