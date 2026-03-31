# Project Lens — Plan

## Overview

**Project Lens** is the **master knowledge base** — a general-purpose project indexer that handles all data ingestion, AI digestion, and indexing. It maintains the complete current + historical state of a project across multiple repos.

It is one half of a two-project system. The other half is **Project PM** (`~/dev/tools/project-pm/`), a consumer that provides the human-facing UX layer (web dashboards, Telegram chatbot, per-person task boards). Project Lens has value independent of Project PM — it's the shareable resource that any tool can consume.

### How They Connect

```
project-lens (this repo)              project-pm
  scripts/github_scan.py                reads from
  scripts/telegram_ingest.py    ───►    project-lens SQLite DB
  cron: AI digestion                    (direct DB access, same machine)
       │
       ▼
  SQLite database
  (raw data, AI digests, assessments,
   contributor profiles, chains, decisions)
```

### Why Split?

- The indexer has value **independent of the PM agent** — it's the shareable resource, other tools can consume it
- The PM agent is one of potentially many consumers
- The indexer handles the complex data pipeline; the PM agent handles UX
- Different deployment lifecycle — the indexer runs headless; the PM agent needs public access (Cloudflare Tunnel)
- Re-indexing the knowledge base doesn't affect the PM UX layer and vice versa

### Directory Layout

```
~/dev/tools/
  project-lens/              # the knowledge base engine (this repo)
    backend/                 # models, config, database
    scripts/                 # data ingestion + AI digestion scripts

  project-pm/                # the PM agent / UX layer (separate repo)
    backend/                 # FastAPI app, services
    templates/               # Jinja2 + HTMX
```

Project-specific config lives in separate small public repos:

```
project-lens-seedsigner-config/    # public repo
  config.yaml                       # project setup: repos, data sources
  .env                              # secrets (gitignored)
```

The tool repo (`project-lens`) is generic and contains no project-specific data. Config repos are public so others can see how the project is set up. Secrets are gitignored.

---

## First Two Project Instances

1. **SeedSigner (public repos)**: ~195 open issues, ~30 open PRs, 2-3 maintainers (all part-time volunteers except kdmukai). Multi-person with ping-pong review flow, monthly team calls, Telegram dev group, GitHub project board.

2. **SeedSigner MicroPython Port (private repos)**: kdmukai's solo effort across seedsigner-c-modules, seedsigner-micropython-builder, seedsigner-raspi-lvgl, esp-board-common. No multi-person interaction — value is master cross-repo view, work item suggestions, and accomplishment tracking.

---

## Responsibilities

- Initial bulk indexing of all historical data (PRs, issues, git history, Telegram)
- Ongoing live updates (GitHub polling, Telegram ingestion) via cron
- AI digestion (summarization/characterization) of all ingested data
- ChromaDB embedding collections
- SQLite database with raw data, digests, PR assessments, contributor profiles, issue assessments
- **Current project state** including blocker detection, ping-pong tracking, review status — this is project state, not PM-specific
- Dedicated git clones of all tracked repos (clean, always on default branch)
- Data exposed via direct DB access (same machine as PM agent)

---

## GitHub Access Without an Account

The `kdmukAI-bot` GitHub account is currently suspended. All SeedSigner repos are public, so the system must work with **unauthenticated access only**:

- **GitHub REST API (unauthenticated)**: 60 requests/hour rate limit. Sufficient for periodic scanning of 6 repos.
- **`gh` CLI**: requires authentication — **not usable** until the account is restored.
- **GitHub MCP tools**: require authentication — **not usable** until the account is restored.
- Helper scripts use `httpx` to call the GitHub REST API directly with no auth token. Public repo endpoints (PRs, issues, comments, reviews) are all accessible.

**Incremental fetching**: Each scan only fetches activity since the last successful scan. The DB stores a `last_synced` timestamp per record. API calls use:
- `GET /repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=desc` — sorted by updated, stop when we reach PRs older than last sync
- `GET /repos/{owner}/{repo}/issues?state=open&sort=updated&direction=desc&since={last_synced}` — only issues updated since last scan
- For reviews/comments on changed PRs: only fetch for PRs whose `updated_at` is newer than our cached version
- If nothing changed in a repo, that repo costs 1-2 API calls (the list endpoints return empty or unchanged)

**Rate limit impact**: A typical incremental scan with 0-3 changed PRs across all repos needs ~10-15 API calls total, well within the 60/hour unauthenticated limit. First-time full scan is heavier (~30-50 calls) but only happens once.

Once kdmukAI-bot is restored, we can switch to authenticated access (5,000 requests/hour) and re-enable GitHub MCP tools in the cron prompts.

---

## Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Background analysis | Claude Code via cron (`claude -p`) | Built-in AI analysis + script execution. Cron prompts orchestrate helper scripts that do the data gathering and DB writes. |
| GitHub data | httpx + GitHub REST API (unauthenticated) | Public repos, 60 req/hr limit. Helper scripts handle API calls directly — no auth needed. Switch to GitHub MCP when account is restored. |
| Database | SQLite (async SQLAlchemy + aiosqlite) | Proven pattern. Single file, no daemon, easy backup. PM agent reads from this same file. |
| Embeddings | ChromaDB (already running locally for codebase-context-mcp) | Separate collections per data source per project instance |
| Deployment | crontab (workers) | Analysis and ingestion as cron jobs. No web server needed — PM agent handles that. |

---

## Database Schema

```
project_config  — governance settings copied from config YAML on each scan
                  fields: key, value (JSON)
                  Stores merge_authorities, trust_tiers, and other governance
                  data that consumers (PM agent) need without reading the YAML
                  directly. YAML is the editable source; DB is the distribution
                  mechanism.

github_prs      — cached PR state (synced every 4 hours)
                  fields: repo, number, title, author, state, labels (JSON),
                  review_state, mergeable, diff_size, body,
                  created_at_gh, updated_at_gh, closed_at_gh, merged_at_gh,
                  last_synced,
                  ci_status (passing|failing|pending|none — from check-runs API),
                  ci_commit_sha (HEAD commit the CI status refers to — if PR head
                  has moved past this, the status is stale and needs re-fetch)

github_issues   — cached issue state (synced every 4 hours)
                  fields: repo, number, title, author, state, labels (JSON),
                  comment_count, body,
                  created_at_gh, updated_at_gh, closed_at_gh,
                  last_synced

pr_assessments  — AI-generated analysis per PR
                  fields: repo, pr_number, tier (0-3),
                  merge_target (login of merge authority from project config, or "blocked"),
                  reviews (JSON: [{user, type, date}]), tested_on_hardware,
                  complexity, risk, summary, recommendation,
                  participant_contributions (JSON — per-person tagged contributions),
                  lessons_learned (JSON — design principles/process lessons from review),
                  outcome (JSON — category, impact, whether review improved result),
                  cross_references (JSON — links to related PRs/issues/discussions),
                  pending_actions (JSON — per-person open items, updated every scan;
                    see PR Review State Tracking below),
                  pending_actions_updated_at,
                  merge_readiness (not_ready|possibly_ready|merge_ready — AI-generated;
                    see Merge Readiness below),
                  merge_readiness_rationale (brief explanation for the classification),
                  reviewer_sentiment (JSON — AI-generated, see below),
                  stale_reason (neglected|indecisive|soft_rejected|null — AI-generated)

                  participant_contributions structure:
                    [{github_username, role (author|reviewer_primary|reviewer_secondary|...),
                      contributions: [{type, detail}]}]
                    Contribution types: architectural_guidance, bug_catch, code_suggestion,
                    tested_ack, ack, edge_case_found, naming_improvement,
                    test_coverage_request, mentoring, process_feedback, hardware_tested,
                    security_review, feature_implementation, responsive_to_feedback, ...
                    Deduplicate by substance — if the same point is made across multiple
                    comments, describe it once. Preserve distinct contributions even if
                    they share the same type.

                  outcome structure:
                    {category (feature|bugfix|refactor|cleanup|security|docs|l10n|...),
                     impact (what changed for users or the codebase),
                     review_improved_outcome (bool),
                     review_improvement (how the review process made the result better)}

                  cross_references structure:
                    [{type (spawned|depends_on|fixes|related_to|discussed_in),
                      target (e.g. "SeedSigner/seedsigner#850", "issue#870"),
                      note (optional context)}]

                  lessons_learned structure:
                    [{category (architecture|process|code_quality|testing|security|...),
                      principle (the reusable takeaway),
                      detail (specific context from this PR),
                      source (who articulated it)}]

issue_assessments — AI-generated analysis per issue
                  fields: repo, issue_number,
                  status (active|stale|resolved|obsolete),
                  staleness_days, comment_count, last_commenter,
                  summary (digested discussion: key points, decisions, current state),
                  related_prs (JSON: PRs that address or reference this issue),
                  suggested_action (close|triage|respond|link-to-pr|none),
                  suggested_action_reason

chains          — dependency chains (multi-repo efforts)
                  fields: slug, title, status, steps (JSON with completed steps preserved),
                  blockers, next_action

decisions       — recorded decisions from calls/Telegram/GitHub
                  fields: topic, decision, rationale, decided_by, source, related_prs

telegram_msgs   — ingested group chat messages
                  fields: message_id, from_user, text, date,
                  is_processed, extracted_actions

api_usage       — Claude Code usage tracking
                  fields: purpose, model, input_tokens, output_tokens, estimated_cost

git_commits     — indexed commit history from local clones
                  fields: repo, hash, author_name, author_email, date,
                  message, files_changed (JSON: [{path, insertions, deletions, status}]),
                  insertions, deletions, is_merge

ai_digests      — persistent cache for AI-generated summaries/characterizations
                  Three-layer architecture: Raw Data → AI Digests (this) → Embeddings.
                  Digests survive re-embedding, pipeline failures, and schema changes.
                  Content hashing ensures unchanged source material is never re-analyzed.
                  fields: source_type (git_commit|github_pr|github_issue|telegram_batch|...),
                  source_ref (natural key for the source record),
                  digest_type (summary|characterization|assessment|...),
                  content_hash (SHA-256 of source material — skip AI if hash matches),
                  digest_text (the AI-generated content),
                  model (which model generated it),
                  input_tokens, output_tokens
                  unique constraint: (source_type, source_ref, digest_type)

analysis_runs   — tracks when background scans ran
                  fields: run_type, started_at, completed_at, status, summary, error
```

---

## Semantic Layer: Embeddings + Vector Search

The SQL database handles structured state (statuses, dates, relationships). But many of the system's intelligent behaviors require **semantic understanding** — knowing what things are about, not just their metadata.

### What Needs Embeddings

| Capability | Why Embeddings Help |
|---|---|
| Contributor-to-PR matching | Embed a contributor's past diffs/reviews. Embed a new PR's diff. Find contributors whose past work is semantically similar — they'll give the best review. |
| Contribution characterization | Embed review comments. At query time, Claude reads them and synthesizes "caught a bug in validation logic" — but embeddings help **retrieve** the relevant comments from a large history. |
| Related item detection | Embed PR descriptions and issue bodies. Find PRs that address the same issue, competing approaches, duplicate efforts. |
| Telegram/decision search | "What did we say about SeedXOR?" → semantic search over ingested messages and decision logs. |
| Dependency chain suggestions | "This PR touches the same files/concepts as this chain" — embeddings surface connections that aren't explicitly linked. |

### Design Philosophy: Capture and Characterize Everything

The goal is a **master embedding index for all development activity**. Ingest everything, embed the digested/summarized version for high-signal retrieval, store the raw data for reference. The richer the index, the more questions the system can answer and the better connections it can surface. Applications built on this index can evolve over time.

### Architecture

- **ChromaDB** (already running locally for codebase-context-mcp) — separate collections per data source per project instance
- **Embedding model**: same pipeline as codebase-context-mcp (sentence-transformers or similar)

**Collections (separate indices per data source):**

| Collection | Contents | Shareable? | Update cadence |
|---|---|---|---|
| `github_prs` | PR descriptions, diff summaries, review discussion digests (open + closed) | Yes | Frequent (every cron scan) |
| `github_issues` | Issue bodies, discussion digests, resolution summaries | Yes | Frequent |
| `github_commits` | Per-file history, release changelogs, significant commit messages, blame aggregates | Yes | Stable (history rarely changes) |
| `telegram` | Discussion digests from dev group (Dec 2022+) | Separate — can be excluded from public exports if needed | Frequent |
| `decisions` | Team call transcripts, extracted decisions, agreements | Separate — may contain unreleased plans | As recorded |
| `contributors` | Contributor profiles, activity summaries, expertise areas | Yes | Weekly |

**Benefits of separation**: share selectively (export GitHub collections publicly, hold back Telegram/decisions if needed); re-index one collection without touching others; different update cadences; independent size management.

**What gets ingested and embedded** (everything practical):
- PR descriptions and **diff summaries** (not raw diffs — summarized: files affected, concepts modified, risk areas)
- **All PRs — open AND closed/merged** — historical PRs contain review discussions, design decisions, rejected alternatives, and the context behind how the codebase got to its current state. One-time backfill to ingest the full PR history, then incremental going forward.
- Review comments and review thread digests
- Issue bodies and **discussion digests** — issues with long comment histories get summarized
- Commit messages (aggregated per PR)
- Telegram discussions (batched by topic, summarized by Haiku)
- Decision log entries from calls and discussions
- Release notes and changelogs
- README/documentation content from each repo
- Contributor activity profiles (synthetic text: what they've worked on, areas of expertise)
- GitHub project board state and history
- **Git history** (from dedicated clones):
  - Per-file/directory history summaries: who contributed, how much, when, major changes
  - Release-to-release changelogs (what changed between tags)
  - Significant commit messages that explain architectural decisions
  - Merge commit messages (often contain PR context)
  - Git blame aggregates per file (who has the most familiarity)
  - NOT individual "fix typo" / "merge branch" / bot commits (too noisy)

**What stays as raw data only** (not embedded):
- Full PR diffs — available on-demand from dedicated git clones (separate from dev clones, always clean)
- Raw Telegram messages (stored in DB, summaries are embedded)
- CI/build logs (only failure summaries embedded if relevant)
- Binary/visual assets (screenshots, images — excluded entirely, low value for the space)

**When embeddings are computed**: during cron scans, incrementally. First full backfill is heaviest; subsequent runs only process new/changed items.

### Cost Management for AI Digestion

The ingestion pipeline has an AI step: Claude Code cron jobs generate summaries/characterizations of raw data before embedding. This runs on the Claude Code subscription, not direct API billing.

- **Three-layer architecture**: Raw Data → AI Digests (cached) → Embeddings. The digests are stored persistently. If you need to re-embed (new embedding model, different chunking), you re-embed from cached digests — no need to re-run the expensive AI summarization.
- **Use Haiku for digestion** where possible — cheap summarization of review threads, issue discussions, commit batches. Sonnet only for nuanced analysis (PR assessments, complex characterizations).
- **Content hashing** — hash the source content; if the hash matches a previous digest, skip the AI step entirely.
- **Incremental by design** — only digest content that's new or changed since the last run.
- **Backfill can be spread over time** — the initial historical ingestion (300+ PRs, 200 issues, git history) doesn't need to happen in one run. Batch it over several days to spread the cost.

### The Two-Layer Pattern

1. **Vector search** retrieves relevant items (e.g., "find the 5 review comments most related to this PR's error handling")
2. **Claude Code** (via cron prompt) synthesizes those items into human-readable output (e.g., "malcolm caught a bug in the validation logic")

### Integration with Existing Codebase Index

The codebase-context-mcp server (already running, indexing SeedSigner repos in ChromaDB) serves as a **context enrichment layer**. Consumers don't need to re-index the codebase — they query the existing index to understand:
- What part of the codebase a PR modifies (semantic role, not just file names)
- How interconnected or risky the affected code is
- Which contributors have expertise in the affected areas
- What features/subsystems are involved (for richer release notes and characterizations)

Available via MCP tools during Claude Code cron jobs.

### The Index as a Shareable Resource

The resulting database and embedding indices are valuable **beyond just the PM agent**. A comprehensive, AI-digested snapshot of a project's entire history — every PR, every review discussion, every issue thread, every decision, every contributor's journey — is a resource that doesn't exist for most open source projects.

**Distribution**: periodic exports of the database and ChromaDB index as downloadable snapshots. Other developers could use them for:
- Onboarding — quickly understanding the project's history, decisions, and architecture
- Research — studying open source development patterns, contributor dynamics, review practices
- Building other tools — any application that benefits from semantic search over project history
- Historical context — understanding *why* the codebase looks the way it does

The export would be the SQLite database + ChromaDB collection files. A static snapshot doesn't need the PM agent to be running — anyone can load it into their own tools.

The dev Telegram group is public (anyone can join), so Telegram-sourced data can be included in exports alongside GitHub-derived data. All data sources for the SeedSigner instance are public.

---

## Background Workers (Claude Code via crontab)

Each cron job invokes `claude -p "<prompt>"` with a detailed prompt that:
1. Runs a Python helper script to gather fresh data and write to SQLite
2. Claude Code analyzes the data (PR quality, tier classification, assessments)
3. Claude Code runs additional scripts to write AI-generated results to the DB

| Job | Cron Schedule | What It Does |
|---|---|---|
| GitHub Scanner | `17 */4 * * *` (every 4h) | Helper script fetches only PR/issue data updated since last scan (incremental, unauthenticated REST API). Claude Code analyzes changes, generates/updates PR assessments for changed PRs only. Writes to DB via helper scripts. |
| Telegram Ingester | `47 */2 * * *` (every 2h) | Helper script reads new group messages from Telegram Bot API. Claude Code classifies messages, extracts decisions/actions. Writes to DB. |

**Flow for each cron job:**
1. Cron invokes `claude -p "<prompt>"` in the project directory
2. The prompt tells Claude Code to run `python scripts/github_scan.py` (or similar) to gather fresh data
3. Claude Code analyzes the data (PR quality, tier classification)
4. Claude Code runs `python scripts/update_assessments.py` to write AI results to SQLite
5. The PM agent reads from the same SQLite file — zero coupling between indexer and PM

**Cost note**: Each Claude Code cron invocation uses the subscription quota, same as interactive sessions. The GitHub scanner is the heaviest (reads many PRs, generates assessments). Cost will depend on context size per invocation — should be monitored and tuned.

---

## Contributor Trust Tiers (for signal weighting)

- **Core** (lead maintainer, co-maintainers): opinions are decisions
- **Trusted** (regular contributors with established track record): reviews are meaningful signal
- **Known** (contributors with merged PRs): input worth reading
- **New** (everyone else): welcome, not consensus

Trust tiers are **manually curated** (in the PM agent's contributor management, not in Lens) and rarely change. They answer the narrow question: "whose review counts as an approval?"

### AI-Derived Contributor Profiles

Separate from trust tiers, the indexer generates **dynamic contributor profiles** from the indexed data. Open source contributors come and go — someone prolific in 2022 may be completely absent now, and may or may not return. The profile captures this reality:

- **Activity status**: active, sporadic, dormant, gone — based on recency and frequency of commits, reviews, comments
- **Expertise areas**: which parts of the codebase they've worked in, what kinds of changes they make (crypto, UI, testing, l10n, infrastructure)
- **Engagement pattern**: primarily an author vs. reviewer, focused on one subsystem vs. broad, drive-by vs. sustained
- **Current trajectory**: ramping up, steady, winding down — trend over recent months
- **Historical context**: what they contributed during their active period, even if they're now dormant

These profiles are AI-generated (stored in `ai_digests` with `source_type: contributor_profile`) and refreshed periodically as new activity is indexed. They give the PM agent the context to make sensible suggestions — don't suggest a review to someone who hasn't been active in 6 months, but do recognize their past expertise if they re-emerge.

### Trust Tier Recommendations

The AI profiles should also **surface recommendations for trust tier changes** — bridging the gap between the dynamic activity data and the manually curated trust list:

- **Promotion candidates**: a `new` or `known` contributor whose sustained, high-quality activity warrants consideration for `trusted` — e.g., "kaylee has submitted 8 PRs in the last 3 months, 6 merged, consistently addresses review feedback, and has started reviewing others' PRs"
- **Inactivity flags**: a `trusted` contributor whose activity has trailed off — not to revoke trust (their past contributions and knowledge remain valuable), but to note they're currently inactive so the PM agent doesn't route work to them. e.g., "jayne: trusted, last activity 4 months ago — still trusted but not currently available for review assignments"

These are recommendations surfaced to the maintainers, not automatic changes. Trust tier changes remain a human decision.

### Merge Authority

Separate from trust tiers, the project config defines **who can merge and what kinds of PRs they handle**. This is a tiny set (1-3 people/accounts per project) and directly drives the merge readiness classification and PM agent routing.

Example configuration:
```yaml
merge_authorities:
  - login: lead-maintainer
    scope: all            # handles all PRs, especially code/architecture
    description: "Primary technical gatekeeper"
  - login: org-account
    scope: non-technical   # docs, translations, config, contributor-facing content
    description: "Offloads lead maintainer for PRs that don't need deep technical review"
```

The `merge_target` field on `pr_assessments` records which merge authority the AI thinks should handle a given PR. This informs both the merge readiness classification and how the PM agent routes its suggestions — a docs PR at `merge_ready` gets surfaced to the non-technical merge authority, not to the lead maintainer.

Merge authority is configured in the project config (not AI-derived) because it's a governance decision, not an activity pattern.

## Review Credit Heuristic

1. **Always credit** (`w/@username`): left substantive review comments, requested changes, back-and-forth with author
2. **Always credit**: tested locally and said so — "tested," "ACK," "confirmed on hardware," "ran this locally"
3. **Skip**: merge-only with no review engagement (maintainer rubber stamps)
4. **Skip**: drive-by "LGTM" or emoji reaction
5. **Flag for decision**: brief approval after someone else did deep review

---

## PR Review State Tracking

The core value of the PM task board is answering a per-person question: **"what do I need to do?"** The right abstraction isn't a PR-level state machine — it's a per-person list of open items on each PR they're involved in.

### The Consumer's Perspective

A PR with three reviewers doesn't have one state. It has multiple simultaneous conversations, and each participant has their own view:

- **The author** may have 2 unaddressed code review comments from Reviewer A, but is waiting on Reviewer B and Reviewer C to respond to changes already pushed. Both sides of that tally are true at the same time.
- **Reviewer B** may have 3 replies from the author to look at and decide whether the concerns were addressed.
- **Reviewer C** may have approved and have nothing pending.

The PM agent's job is to show each person their open items across all PRs. The data model should serve that directly.

### Data Model: Pending Actions Per Person

The `pending_actions` JSON field on `pr_assessments` stores open items per participant:

```json
{
  "simon": {
    "role": "author",
    "open_items": 3,
    "summary": "2 unaddressed review comments from malcolm, 1 from zoe",
    "last_activity_at": "2026-03-29T09:10:00Z"
  },
  "malcolm": {
    "role": "reviewer",
    "open_items": 0,
    "summary": "waiting on author to address 2 comments",
    "last_activity_at": "2026-03-28T14:22:00Z"
  },
  "zoe": {
    "role": "reviewer",
    "open_items": 2,
    "summary": "author pushed new commits addressing feedback; needs re-review",
    "last_activity_at": "2026-03-27T11:00:00Z"
  },
  "wash": {
    "role": "reviewer",
    "open_items": 1,
    "summary": "author pushed changes after review; re-review would be helpful",
    "last_activity_at": "2026-03-27T16:00:00Z"
  }
}
```

Each person's entry only lists what's on *their* plate. The full picture of who's blocking whom is visible by looking at all participants together — no need to duplicate items across both sides.

**CI failure adds an item to the author's list** — if CI is failing, the author has "fix CI" as an open item regardless of review state.

**No-engagement PRs**: everyone has `open_items: 0` but the author's summary says "no reviewers have engaged yet." The PM agent surfaces this as a PR that needs attention from potential reviewers.

### AI-Generated, Not Mechanical

Pending actions are produced by the AI assessment — the same `claude -p` cron job that generates the rest of the PR assessment. There's no separate lightweight scan. The AI reads the raw review and comment data (`reviews_json`, `review_comments_json`, `issue_comments_json`) and determines:

- **What's actually an open item** — distinguishing substantive concerns that need a response from nits, suggestions, and rhetorical comments. Not every review comment is an action item.
- **Whether commits addressed feedback** — a pushed commit may silently resolve multiple comments without explicit replies in the threads.
- **Reviewer sentiment** — some reviewers use nuanced language ("I'm slightly negative", "I'm skeptical", "not sure this is right but can't think of better"). The AI characterizes what kind of response the author actually needs: code changes vs. stronger argument vs. patience.
- **Neglected PRs** — low/zero engagement, not controversial, just overlooked. No one chose to engage.
- **Indecisive PRs** — active discussion but no convergence. Reviewers going back and forth without resolving direction. Sometimes reviewers look for others to build consensus but interest doesn't materialize.
- **Soft rejected PRs** — open a long time with minimal engagement from a contributor who isn't well-known, effectively dying by neglect. The board should surface this honestly so a maintainer can provide direction or close with a kind explanation.

Pending actions only need updating when the underlying PR data changes (`updated_at_gh` moves forward). A PR with no new activity keeps its existing assessment — nothing to recalculate.

The `reviewer_sentiment` field stores AI-characterized sentiment per reviewer:
```json
[
  {"reviewer": "malcolm", "sentiment": "skeptical", "needs": "stronger case for why this approach is needed"},
  {"reviewer": "zoe", "sentiment": "neutral", "needs": "waiting for malcolm's concerns to be resolved"}
]
```

This is particularly important for **student onboarding** — new contributors may not pick up on subtle review language in long PR threads. The board summarizing "lead dev is open but unconvinced — consider strengthening the case" provides genuinely helpful guidance.

### CI Status

The GitHub scan fetches check-run status for open PRs via the check-runs API endpoint (`GET /repos/{owner}/{repo}/commits/{ref}/check-runs`). One extra API call per open PR, but only for open PRs.

States:
- `passing` — all checks green
- `failing` — one or more checks failed → author needs to fix
- `pending` — checks still running
- `none` — no CI configured for this repo

CI failing adds "fix CI" to the author's pending items unconditionally.

### Merge Readiness

A holistic AI-generated signal for the lead maintainer: how ready is this PR to merge? This isn't a formula — it's the AI synthesizing everything it knows about the PR (complexity, risk, who reviewed, how thorough the review was, CI status, pending actions) into a rough classification.

- **`not_ready`** — too early for the lead maintainer. The review process still has substantial work to do: active unresolved review threads, CI failing, no ACK from a trusted reviewer on a non-trivial PR, reviewer sentiment is negative/skeptical. Most open PRs are `not_ready` at any given time, and that's normal. e.g., "SLIP-39 feature — 18 unaddressed review comments, no re-review after author's changes."
- **`possibly_ready`** — reviewed, tested, and ACKed by others, but the maintainer should look at it himself. The prerequisite for `possibly_ready` is that at least one reviewer has done substantive work — reviewed, tested, or ACKed — and that reviewer's trust tier matters. An ACK from a `trusted` or `core` reviewer is strong signal; an ACK from a `new` contributor or a drive-by "LGTM" doesn't move a PR out of `not_ready` on its own. Without a substantive review from someone the project trusts, it's `not_ready`. The AI's judgment here is whether the review depth is proportional to the PR's complexity and risk. A trusted reviewer ACKed a high-risk change — that's enough to put it on the maintainer's radar, but the risk warrants his own read. e.g., "Moderate refactor, kdmukai ACKed but touches crypto paths — worth a quick look."
- **`merge_ready`** — ready to merge. Trusted reviewer(s) ACKed, CI passing, no open review threads. The maintainer can merge without reading the PR himself. Reserve `merge_ready` for PRs where the review process has clearly done its job. e.g., "Small l10n fix, ACKed by a trusted reviewer, CI green." For trivial PRs (typo fixes, minor cleanups), a single ACK from a known contributor may suffice.

The classification accounts for proportionality — a one-line typo fix doesn't need the same review depth as a cryptographic feature. But the threshold for `merge_ready` is intentionally conservative: if there's meaningful doubt about whether the review was sufficient, it's `possibly_ready`, not `merge_ready`.

The `merge_readiness_rationale` field gives the brief reasoning so the maintainer can see *why* at a glance without re-reading the whole PR. This is what powers the PM agent's "Good morning, would you like to start your day with an easy merge?" workflow — surface the `merge_ready` PRs first, then the `possibly_ready` PRs that might need a closer look.

---

## Dependency Chains

Stored in the `chains` table with a JSON `steps` field. **Completed steps are preserved with dates** — this is the institutional memory that prevents "wait, did we already do that?"

Example chain for "Add Japanese language support":
```yaml
steps:
  - repo: seedsigner-translations
    description: "Japanese .po files"
    status: complete
    pr: 78
    completed: 2026-03-01

  - repo: seedsigner
    description: "Wire Japanese locale into settings"
    status: in-review
    pr: 1234

  - repo: seedsigner-os
    description: "Include Japanese fonts in OS image"
    status: planned

  - repo: seedsigner-settings-generator
    description: "Add Japanese to settings QR options"
    status: planned
```

---

## Bootstrap Plan

### Phase 1a: Foundation — Database + Local Git History
**Goal**: Database exists, scripts index full git history from a local clone of the main seedsigner repo.

- SQLAlchemy models for all indexer tables
- Project config loader (external YAML defines repos to track)
- Database initialization (tables created on first run)
- Dedicated git clone of SeedSigner/seedsigner (managed by the indexer, separate from dev clone)
- `scripts/index_git_history.py`: walks the local clone's git log and indexes commits, authors, file-level change stats, tags/releases
- New DB table: `git_commits` — hash, repo, author, date, message, files_changed (JSON), insertions, deletions
- Run indexer manually, verify commit history lands in DB

Starting with local git history is simpler (no rate limits, no auth, deterministic) and provides the richest initial dataset: every commit, every contributor, every file touched, release-to-release changelogs.

**Deliverable**: Populated SQLite database with full git history of the seedsigner repo.

### Phase 1b: GitHub API Sync (PRs + Issues)
**Goal**: Scripts fetch PR/issue data from GitHub REST API and store alongside git history.

- `scripts/github_scan.py`: fetches PR/issue data from GitHub REST API (unauthenticated, httpx, incremental)
- **Historical backfill of all PRs** — open AND closed/merged. The full PR history (~300+ PRs for seedsigner) is the backbone of the project's institutional memory: review discussions, design decisions, rejected alternatives, contributor interactions. Nearly all commits from ~Q3 2021 onward were merged through the PR process, so the PR history effectively covers the project's entire meaningful development history. Only the earliest project-bootstrapping commits predate the PR workflow.
- Fetch PR metadata, descriptions, review comments, review threads for each PR
- Extend to all tracked repos
- Crontab entry for periodic scanning of open PRs/issues (incremental)
- Run scanner manually first, verify data lands in DB

**Deliverable**: Complete PR history (open + closed) and live issue data across all tracked repos, auto-refreshing via cron for ongoing changes.

### Phase 1c: Translation Completeness Tracking
**Goal**: Parse `.po` files from the seedsigner-translations clone to track per-language translation completeness over time.

- Parse each language's `messages.po`: count total strings, translated, fuzzy, untranslated
- **Two complementary completeness metrics**:
  - **String completion**: percentage of `msgid` entries that have a `msgstr` translation. Answers "how many places in the UI will show untranslated English?"
  - **Word-weighted completion**: count English words in each `msgid`, sum those with translations vs total. Answers "how much actual translation work remains?" A language at 90% string completion but 99% word completion is nearly done (just a few short labels left). A language at 90% string / 75% word has substantial untranslated paragraphs.
- Store as structured data per language per snapshot:
  ```json
  {"language": "cs", "label": "v0.8.7",
   "total_strings": 312, "translated": 280, "fuzzy": 12, "untranslated": 20,
   "string_completion": 89.7,
   "total_source_words": 2847, "translated_source_words": 2690,
   "word_completion": 94.5}
  ```
- Track over time by snapshotting at each indexed commit/tag — "Czech went from 72%/81% to 91%/95% between v0.8.6 and v0.8.7"
- No AI needed — purely mechanical `.po` file parsing
- Run against the dedicated translations clone, triggered alongside git history indexing

**Deliverable**: Per-language translation completeness (both string-based and word-weighted), tracked over time across releases.

### Phase 2: AI Analysis of PRs and Issues
**Goal**: Claude Code cron generates assessments for all indexed PRs and issues. This is the most quota-intensive phase.

**What gets analyzed (and what doesn't):**
- **Every PR** gets an assessment: tier classification, complexity, risk, review state synthesis, narrative digest ("what happened", "where things stand"). This is the core unit of analysis — each PR is a self-contained story with a diff, discussion, and outcome.
- **Every open issue** gets an assessment: staleness detection, related PR linking, suggested actions.
- **Individual git commits do NOT get separate AI analysis.** Since nearly all meaningful commits came through PRs, the PR-level analysis already covers them. The indexed git history (Phase 1a) provides queryable raw data for aggregation — file-level stats, contributor activity, release changelogs — but that data is structural, not something that needs per-commit AI summarization.
- **Pre-PR commits** (early 2021 bootstrapping) are a small set. If they need context, a single aggregate summary covers them — not individual analysis.

**AI analysis approach:**
- Historical backfill: spread over multiple `claude -p` cron runs to manage quota. Each run processes a batch of unanalyzed PRs, generates assessments, stores results in `pr_assessments` table and digests in `ai_digests` table.
- Content hashing: if a PR hasn't changed since its last assessment, skip it.
- Incremental going forward: only re-assess PRs with new activity (comments, reviews, commits).
- Helper script: `scripts/update_assessments.py`

**Quota impact estimate:** ~300 historical PRs + ~200 open issues. Each PR assessment is a medium-context Claude Code prompt (PR description + review comments + diff summary). Spread across cron runs, this is the one-time backfill cost. Ongoing incremental cost is minimal (0-5 changed PRs per scan).

**Deliverable**: AI-generated assessments for all PRs and issues, stored in both `pr_assessments`/`issue_assessments` (structured) and `ai_digests` (cached text). Updated incrementally on each scan.

### Phase 2b: Test Coverage Evolution
**Goal**: Track how test coverage evolved over each PR's lifecycle — from initial submission through review-driven improvements to final merge state.

**Approach:**
- For each merged PR, identify key commit points from the review data: initial submission, post-review pushes, final merged state (typically 2-3 commits per PR)
- For each commit: `git checkout <hash>` in the dedicated clone, run `pytest --cov`, parse the coverage report
- Store per-file and aggregate coverage at each point
- Diff between points to show coverage evolution

**What this reveals:**
- Did the PR test the code it introduced? (new code coverage)
- Did review feedback drive better test coverage? (coverage evolution across review rounds)
- Did the PR improve or regress overall project coverage?
- Context-aware expectations: hardware integration code (`src/seedsigner/hardware/`) is intentionally untested (no hardware in test environment), so low coverage there is expected, not a red flag

**Coverage data structure** (stored on `pr_assessments`):
```json
"coverage_evolution": [
  {"commit": "abc1234", "label": "initial_submission", "date": "2025-11-15",
   "overall": 68.9, "new_code": 42.0, "files_untested": ["views/new_view.py"]},
  {"commit": "def5678", "label": "post_review_1", "date": "2025-11-22",
   "overall": 69.4, "new_code": 78.0, "files_untested": []},
  {"commit": "ghi9012", "label": "final_merged", "date": "2025-12-01",
   "overall": 70.1, "new_code": 91.0, "files_untested": []}
]
```

**Cost**: zero AI, zero API. The test suite runs in seconds. ~1,200 coverage runs (3 per PR × 400 merged PRs) takes 30-60 minutes total as a one-time batch job. Ongoing: run coverage for new PR commits as they're detected by the GitHub scanner.

**Deliverable**: Coverage evolution data for all merged PRs, showing whether review drove better test coverage.

### Phase 3: Telegram Ingestion
**Goal**: Passive group chat monitoring, message classification, and topic-level AI digestion.

**Raw ingestion (no AI):**
- Register Telegram bot, add to dev group (with disclosure)
- **Historical backfill**: export the full chat history (December 2022 to present, 3+ years). Store raw messages in `telegram_msgs` table.
- Group chat reader: `python-telegram-bot` long-polling, stores messages in DB going forward

**AI classification and digestion (uses quota):**
- Claude Code cron batches raw messages by topic/timeframe and generates digests: decisions, action items, context summaries
- Decisions weighted by who said them (core > trusted > known > new)
- AI digests stored in `ai_digests` table with content hashes — batched by topic, not per-message
- Historical backfill spread across multiple cron runs (3+ years of messages)

**Deliverable**: Telegram messages ingested, classified, and digested into topic-level summaries with decisions extracted.

### Phase 4: Semantic Layer (Embeddings + Vector Search)
**Goal**: Embeddings over all digested content for semantic retrieval, plus a small number of aggregate summaries.

The bulk of this phase operates on **already-generated AI digests** from Phases 2 and 3 — embedding is cheap (just read cached digests and compute vectors, no AI calls). A small amount of new AI work generates aggregate summaries from the indexed git history.

- ChromaDB collections for PRs, issues, Telegram, decisions, contributors
- Embed from cached `ai_digests` table — no new AI calls for previously digested content
- Content hashing: only re-embed digests that have changed
- **Aggregate git history summaries** (small number of new AI prompts):
  - Per-file/directory contribution summaries (who worked on what, derived from indexed commits)
  - Release-to-release changelogs (diff between tags, summarized)
  - Contributor expertise profiles (synthesized from commit + PR + review data)
- Integration with existing codebase-context-mcp index

**Deliverable**: Full semantic search capability over project history, queryable by PM agent and other consumers.

---

## Claude Usage Strategy

**Claude Code Max subscription ($200/mo)** — push all bulk work through Claude Code to use the subscription quota rather than per-token API billing.

| Component | Mechanism | Billing |
|---|---|---|
| GitHub scanning & PR assessment | `claude -p` cron jobs | Subscription quota |
| AI digestion for embeddings | `claude -p` cron jobs | Subscription quota |
| Telegram message classification | `claude -p` cron jobs | Subscription quota |
| Historical backfill (one-time) | `claude -p` batched runs | Subscription quota |

All indexer work runs through Claude Code on the subscription. No direct API billing.

---

## Verification

### After Phase 1a
- Full git history of seedsigner repo indexed in DB
- Commit count, contributor list, file change stats all queryable
- Per-file blame/contribution data available

### After Phase 1b
- Complete historical PR data (open + closed) for all tracked repos in DB
- PR descriptions, review comments, review threads all stored
- Open issues synced, refreshing every 4 hours via cron
- Incremental fetching works (subsequent scans are fast)

### After Phase 2
- Every historical PR has an AI assessment (tier, complexity, risk, narrative digest)
- AI digests stored in `ai_digests` table with content hashes — re-runnable without re-analyzing
- Issue assessments detect staleness and suggest actions
- Assessments update incrementally when underlying data changes
- No per-commit AI analysis (PR assessments cover the commit history since ~Q3 2021)

### After Phase 3
- Group chat messages are being captured and classified
- Decisions extracted from group chat appear in the decisions table
- Historical backfill covers Dec 2022 to present
- AI digests for Telegram batched by topic, stored in `ai_digests`

### After Phase 4
- Semantic search returns relevant results across all data sources
- "What did we say about SeedXOR?" returns Telegram discussion digests
- Contributor expertise profiles match actual contribution history
- Aggregate git history summaries (per-file, per-release) generated and embedded
- All embeddings derived from cached `ai_digests` — no redundant AI calls
