# Project Lens

Open source projects accumulate years of institutional knowledge that's scattered across git history, PR discussions, issue threads, and chat logs. The design decisions, the rejected alternatives, the lessons reviewers taught contributors — it's all there, but buried in thousands of individual conversations that nobody can search or reason about.

Project Lens indexes all of that and uses AI to extract the substance: what each PR actually accomplished, who contributed what, what design principles emerged from review, how the project's practices evolved over time. The result is a queryable knowledge base that makes a project's full history accessible — for onboarding, for project management, for understanding *why* the codebase looks the way it does.

The database is designed to be consumed by AI-powered applications — task boards that generate per-person suggestions, chatbots that answer project questions with full historical context, release note generators that know not just what merged but who contributed what and why it matters. AI consumers can reason from curated, pre-assessed data rather than raw API dumps.

Project Lens provides the enriched foundation; you build the experience on top.

## How it works

You point Project Lens at a set of GitHub repos via a simple YAML config. It clones them locally and indexes the full git history — every commit, every contributor, every file touched. Then it fetches the complete PR and issue history from the GitHub API, including all review comments and discussion threads.

That gives you the raw data. The next step is where it gets interesting: an AI analysis pass processes each PR and issue and produces structured assessments — not just metadata like "3 reviews, 200 lines changed" but a narrative of what happened, who contributed what, whether the review process improved the result, what design principles emerged, and how different items relate to each other. A PR with 30 review comments becomes a concise story: "kdmukai guided a new contributor through an architectural redesign, jdlcdl's testing found a derivation path bug, the final implementation was substantially better than what was originally submitted." An issue with a long discussion thread gets an honest characterization of where things stand — who weighed in, what positions they took, whether anything was decided, and whether the conversation simply fizzled out.

All of this lands in a single SQLite database. The raw data is preserved alongside the AI-generated assessments, and content hashing ensures unchanged data is never re-analyzed. The result is a complete, queryable snapshot of a project's development history — structured for AI consumption but also useful for direct SQL queries.

All digested content is then embedded into a vector database (ChromaDB) for semantic search — finding related PRs and issues, surfacing relevant past discussions across data sources, and connecting threads of work that span GitHub and Telegram. The AI assessments are cached independently from the embeddings, so re-embedding with a different model or chunking strategy never requires re-running the expensive analysis step.

Project Lens is generic — the engine knows nothing about any specific project. All project-specific configuration (which repos, secrets, the database itself) lives in a separate config repo. You can run multiple instances for different projects from the same codebase.

## Example assessment

Here's a real assessment of [SeedSigner PR #740](https://github.com/SeedSigner/seedsigner/pull/740). The raw PR accumulated 58 review and discussion comments across multiple revision rounds over several weeks. Project Lens distills them into:

```json
{
  "pr_number": 740,
  "tier": 3,
  "complexity": "high",
  "risk": "medium",
  "summary": "Disables screensaver during SeedQR transcription to prevent interrupting
    a security-critical manual process. Extensive review drove significant architectural
    changes. Multiple revision rounds over several weeks.",
  "outcome": {
    "category": "feature",
    "impact": "Prevents screensaver from interrupting SeedQR manual transcription — a
      flow where users are hand-copying seed data",
    "review_improved_outcome": true,
    "review_improvement": "kdmukai's review completely changed the architecture from
      exposing internal state to a per-View property, resulting in better encapsulation"
  },
  "cross_references": [
    { "type": "discussed_in", "target": "telegram:seedsigner_dev",
      "note": "Summer of Bitcoin idea discussed in dev group" }
  ],
  "participant_contributions": [
    { "github_username": "kdmukai", "role": "reviewer_primary",
      "contributions": [
        { "type": "architectural_guidance",
          "detail": "Drove redesign toward encapsulated per-View property pattern
            instead of exposing internal state" },
        { "type": "mentoring",
          "detail": "Guided new contributor through multiple revision rounds with
            detailed explanations of design rationale" },
        { "type": "process_feedback",
          "detail": "Requested self-contained PR context and test coverage" }
      ]
    },
    { "github_username": "FazleRabbbiferdaus172", "role": "author",
      "contributions": [
        { "type": "feature_implementation",
          "detail": "Implemented screensaver disable during SeedQR transcription" },
        { "type": "responsive_to_feedback",
          "detail": "Adapted design across multiple rounds from framework-style
            pattern to simpler per-View property" }
      ]
    },
    { "github_username": "newtonick", "role": "reviewer_secondary",
      "contributions": [
        { "type": "test_coverage_request",
          "detail": "Requested test coverage for screensaver suppression behavior" },
        { "type": "ack", "detail": "Final ACK and merge" }
      ]
    }
  ],
  "lessons_learned": [
    { "category": "architecture",
      "principle": "Don't expose internal state — ask the object a question instead",
      "detail": "Rather than exposing Controller.active_view, add a method that
        encapsulates the decision",
      "source": "kdmukai" },
    { "category": "process",
      "principle": "PRs should be self-contained with linked context",
      "detail": "When a PR originates from an external discussion, link to it so
        reviewers don't need tribal knowledge",
      "source": "kdmukai" }
  ]
}
```

## Getting started

```bash
# Install
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure — point the tool at your project config repo (see below)
cp .env.example .env
# Edit .env: set DEFAULT_CONFIG_DIR=/path/to/your-project-config

# Run the full GitHub pipeline (or run individual steps — see Ingestion below)
./scripts/pipelines/github-pipeline.sh /path/to/your-project-config
```

## Project config

Each project you want to index gets its own config directory (typically a separate repo) that holds project-specific configuration, secrets, and all generated data. Project Lens is generic — the tool knows nothing about any specific project.

Create a directory for your project and add two files:

**`config.yaml`** — which repos to track and who the gatekeepers are:

```yaml
project:
  name: myproject
  display_name: My Project

merge_authorities:
  - login: lead-maintainer
    scope: technical
    description: "Primary technical gatekeeper for code PRs"
  - login: org-account
    scope: non-technical
    description: "Handles docs, translations, config PRs"

repos:
  - owner: myorg
    name: main-repo
  - owner: myorg
    name: other-repo
```

`merge_authorities` defines who can merge PRs and what kinds they handle. This drives the AI's merge readiness classification — it needs to know who the gatekeepers are and what scope each covers. The config is synced to the database on each scan so downstream consumers (dashboards, chatbots) can read it without accessing the YAML directly.

**`.env`** — secrets and optional overrides:

```bash
GITHUB_TOKEN=ghp_...

# Optional: Telegram ingestion
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...

# Optional overrides (derived automatically from config dir if not set)
# DATABASE_URL=sqlite+aiosqlite:///var/project-lens.db
# CLONES_DIR=var/clones
# TELEGRAM_DATABASE_URL=sqlite+aiosqlite:///var/telegram.db
# EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
# CHROMA_DIR=var/chroma
```

Set `GITHUB_TOKEN` for authenticated API access (5,000 req/hr vs 60 unauthenticated). All other settings have sensible defaults derived from the config directory.

Project Lens finds the config directory via (in priority order):
1. `--config-dir /path/to/config` command-line argument
2. `PROJECT_CONFIG_DIR` environment variable (used by the pipeline scripts)
3. `DEFAULT_CONFIG_DIR` in the tool repo's `.env` (dev convenience)

The resulting directory structure after indexing:

```
your-project-config/
  config.yaml       # project configuration
  .env               # secrets (gitignored)
  var/
    project-lens.db  # SQLite database
    telegram.db      # Telegram messages (separate for selective sharing)
    clones/          # dedicated git clones
    chroma/          # ChromaDB vector embeddings
```

## Ingestion

Once configured, ingestion runs in a prescribed order. Each step builds on the data from previous steps.

```bash
# 1. Clone repos and index full git history (seconds, no API calls)
python scripts/github/ingest/index_git_history.py

# 2. Fetch all PRs, issues, reviews, and comments from GitHub API
#    First run fetches complete history; subsequent runs are incremental
python scripts/github/ingest/github_scan.py

# 3. Extract contributor profiles from git + GitHub data
#    Resolves identities across systems, computes per-dimension activity tiers
python scripts/github/process/extract_contributors.py

# 4. AI assessment of every PR
#    Currently uses Claude Code in headless mode; adaptable to other AI providers
#    Resumable — picks up where it left off if interrupted
./scripts/github/ai_assess/batch_assess.sh

# 5. AI assessment of every issue
#    Same pattern as PR assessment — batched, resumable
./scripts/github/ai_assess/batch_assess_issues.sh

# 6. Ingest Telegram group chat history (if applicable)
#    Import from Telegram Desktop JSON export, then run bot for ongoing messages
python scripts/telegram/ingest/telegram_ingest.py --import-json /path/to/result.json

# 7. AI digestion of Telegram discussions
#    One batch per invocation; loop script handles iteration
./scripts/telegram/ai_assess/telegram_digest.sh

# 8. Test coverage at each PR's initial submission and merge point
#    Only for repos with a pytest-based test suite
python scripts/github/process/calculate_coverage.py

# 9. Embed all digested content into ChromaDB for semantic search
#    Incremental — only embeds new or changed content using content hashing
python scripts/embed_digests.py
```

**Steps 1-3** are fast and free — just git operations, GitHub API calls, and local computation. Step 2 is rate-limited by the GitHub API but only needs a full fetch once; after that, it incrementally fetches only PRs with new activity.

**Step 3** (contributor extraction) must run after steps 1 and 2 because it derives profiles from both git history and GitHub PR data. It resolves contributor identities across systems (git author names/emails → GitHub logins), then scores each contributor across three independent dimensions using statistical bands:

- **Merge authority** — who merges other people's PRs (merge commits)
- **Shipped code** — whose authored PRs get accepted and merged
- **Review engagement** — total PR interaction (formal reviews + inline code comments + discussion comments)

Each dimension is scored as high/moderate/low/none based on the project's own distribution (mean + 1 standard deviation thresholds). This produces contributor profiles that downstream AI steps (PR assessments, Telegram digestion) use to understand who the key people are and what roles they play — without any manual configuration.

**Steps 4-5** are the AI analysis passes — they read stored PR and issue data from the database, generate structured assessments, and write them back. Each item is committed individually, so progress is never lost. Issue assessments follow a different schema than PR assessments, tuned for the reality that issue discussions often don't reach conclusions — the AI honestly characterizes stale discussions, notes when nobody with authority weighed in, and avoids imposing a narrative of progress where none exists.

**Steps 6-7** handle Telegram group chat ingestion. Step 6 imports historical messages from a Telegram Desktop JSON export into a separate database (so chat data can be selectively shared independent of GitHub data). Step 7 runs an AI analysis that processes messages in weekly batches, extracting substantive discussion threads and discarding noise — one batch per `claude -p` invocation for fresh context each time, with the loop script handling iteration. The AI uses the contributor profiles from step 3 to understand who the key participants are. For ongoing ingestion, the Telegram bot can run in polling mode (`--poll`) to capture new messages in real time.

**Step 8** is optional — only applicable to repos with pytest-based test suites.

**Step 9** embeds all AI-digested content into ChromaDB for semantic search. This includes PR assessment summaries, issue assessment summaries, and Telegram thread digests. The embedding script uses content hashing to detect changes — new items get added, modified items get re-embedded, and unchanged items are skipped. Uses `BAAI/bge-large-en-v1.5` on GPU if available. ChromaDB storage lives alongside the main database in the config repo's `var/chroma/` directory.

All steps are incremental and idempotent, so they can be run on a schedule to keep the index current as the project evolves.

## Pipelines

Two pipeline scripts orchestrate the individual steps into end-to-end runs, suitable for cron scheduling. Both take the config directory as an argument and log to `<config-dir>/var/logs/`.

**GitHub pipeline** — runs steps 1-5 + 9 in order:

```bash
./scripts/pipelines/github-pipeline.sh /path/to/your-project-config
```

Pulls new commits, fetches new PRs/issues from the GitHub API, rebuilds contributor profiles, runs AI assessment on anything new, and updates embeddings. Uses `set -e` — if any step fails, the pipeline stops immediately rather than running downstream steps on incomplete data.

**Telegram pipeline** — runs steps 7 + 9:

```bash
./scripts/pipelines/telegram-pipeline.sh /path/to/your-project-config
```

Processes accumulated Telegram messages into weekly thread digests and updates embeddings. Only processes weeks that have fully closed — the current in-progress week is skipped to avoid analyzing an incomplete batch.

### Scheduling

Both pipelines pass the config directory via the `PROJECT_CONFIG_DIR` environment variable, which all scripts pick up automatically. Example crontab:

```
# Project Lens (MyProject): GitHub scan + AI assessment — every day at 2:00 AM
0 2 * * * /path/to/project-lens/scripts/pipelines/github-pipeline.sh /path/to/myproject-config

# Project Lens (MyProject): Telegram digest — every Friday at 4:00 AM
0 4 * * 5 /path/to/project-lens/scripts/pipelines/telegram-pipeline.sh /path/to/myproject-config
```

The pipeline scripts handle PATH setup for cron environments and activate the Python venv internally.

## What's in the database

Project Lens uses two SQLite databases. The main database holds GitHub data, assessments, and contributor profiles. Telegram messages go in a separate database so they can be selectively shared.

**Main database:**

- **git_commits** — full commit history from local clones (authors, file changes, insertions/deletions)
- **github_prs** — PR metadata, bodies, review comments, issue comments (complete API data as JSON)
- **github_issues** — issue metadata, bodies, and comment threads (complete API data as JSON, open and closed)
- **contributor_profiles** — data-derived profiles per contributor: GitHub login, display names seen across systems, activity stats, and per-dimension tier scores (merge authority, shipped code, review engagement)
- **pr_assessments** — AI-generated analysis per PR: tier, complexity, risk, narrative summary, per-participant contributions, lessons learned, outcome assessment, cross-references to related PRs/issues, test coverage evolution from initial submission to merge. For open PRs: per-person pending actions (what's on each participant's plate), merge readiness classification (not_ready/possibly_ready/merge_ready), per-reviewer sentiment, and staleness detection.
- **issue_assessments** — AI-generated analysis per issue: tier, category (bug/feature_request/question/discussion/task/support), complexity, impact, narrative summary, discussion state (active/stale/resolved/wont_fix/duplicate), stale reason when applicable, per-participant contributions, discussion positions capturing each participant's stance, cross-references to related PRs and issues, and suggested labels. Designed for the reality that issue discussions often don't reach conclusions — honestly characterizes when discussions fizzled out or went nowhere.
- **ai_digests** — cached AI outputs with content hashes, so unchanged data is never re-analyzed. Includes PR assessment digests, issue assessment digests, and Telegram thread summaries.
- **project_config** — governance settings (merge authorities) synced from the project config YAML on each scan
- **analysis_runs** — when each scan or indexing job ran

**Telegram database** (separate file):

- **telegram_msgs** — ingested group chat messages with sender info, reply threading, message type, and reaction data (who reacted with what emoji)

**ChromaDB** (vector database for semantic search):

- **telegram_threads** — embedded thread summaries from Telegram digestion, searchable by topic and content
- **pr_assessments** — embedded PR assessment summaries, searchable by what the PR did, who was involved, and what was learned
- **issue_assessments** — embedded issue assessment summaries, searchable by topic, discussion state, and participant positions

Collections use cosine similarity with `BAAI/bge-large-en-v1.5` embeddings and hybrid search (embedding similarity + inverse-frequency keyword boost). Content hashing ensures only changed documents are re-embedded on each run.

Contributor roles and expertise are not manually assigned — they emerge from analysis of the indexed data.

## Planned

- **Translation completeness tracking** — parse `.po` files to track per-language translation progress over time, with both string-based and word-weighted completion metrics.

## License

MIT
