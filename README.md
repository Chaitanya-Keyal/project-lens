# Project Lens

Open source projects accumulate years of institutional knowledge that's scattered across git history, PR discussions, issue threads, and chat logs. The design decisions, the rejected alternatives, the lessons reviewers taught contributors — it's all there, but buried in thousands of individual conversations that nobody can search or reason about.

Project Lens indexes all of that and uses AI to extract the substance: what each PR actually accomplished, who contributed what, what design principles emerged from review, how the project's practices evolved over time. The result is a queryable knowledge base that makes a project's full history accessible — for onboarding, for project management, for understanding *why* the codebase looks the way it does.

The database is designed to be consumed by AI-powered applications — task boards that generate per-person suggestions, chatbots that answer project questions with full historical context, release note generators that know not just what merged but who contributed what and why it matters. AI consumers can reason from curated, pre-assessed data rather than raw API dumps.

Project Lens provides the enriched foundation; you build the experience on top.

## How it works

You point Project Lens at a set of GitHub repos via a simple YAML config. It clones them locally and indexes the full git history — every commit, every contributor, every file touched. Then it fetches the complete PR and issue history from the GitHub API, including all review comments and discussion threads.

That gives you the raw data. The next step is where it gets interesting: an AI analysis pass processes each PR and produces a structured assessment — not just metadata like "3 reviews, 200 lines changed" but a narrative of what happened, who contributed what, whether the review process improved the result, what design principles emerged, and how different items relate to each other. A PR with 30 review comments becomes a concise story: "kdmukai guided a new contributor through an architectural redesign, jdlcdl's testing found a derivation path bug, the final implementation was substantially better than what was originally submitted."

All of this lands in a single SQLite database. The raw data is preserved alongside the AI-generated assessments, and content hashing ensures unchanged data is never re-analyzed. The result is a complete, queryable snapshot of a project's development history — structured for AI consumption but also useful for direct SQL queries.

The next layer will embed all of this digested content into a vector database for semantic search — finding related PRs, surfacing relevant past discussions, and building contributor expertise profiles. The AI assessments are cached independently from the embeddings, so re-embedding with a different model or chunking strategy never requires re-running the expensive analysis step.

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

# Configure
cp .env.example .env
# Edit .env to point PROJECT_CONFIG_DIR at your config repo (see below)

# Index
python scripts/index_git_history.py        # clone repos, index git history
python scripts/github_scan.py              # fetch PRs, issues, reviews from GitHub API
./scripts/run_batch_assess.sh              # AI assessment of all PRs
```

## Project config

Each project you want to index gets its own config repo that holds the configuration and all generated data:

```
project-lens-myproject-config/
  config.yaml       # which repos to track
  .env               # GITHUB_TOKEN (gitignored)
  var/
    project-lens.db  # the SQLite database
    clones/          # dedicated git clones
```

The config is minimal:

```yaml
project:
  name: myproject
  display_name: My Project

repos:
  - owner: myorg
    name: main-repo
  - owner: myorg
    name: other-repo
```

Set `GITHUB_TOKEN` in `.env` for authenticated API access (5,000 req/hr vs 60 unauthenticated).

## Ingestion

Once configured, ingestion happens in three steps:

```bash
# 1. Clone repos and index full git history (seconds, no API calls)
python scripts/index_git_history.py

# 2. Fetch all PRs, issues, and review comments from GitHub API
#    First run fetches complete history; subsequent runs are incremental
python scripts/github_scan.py

# 3. AI assessment of every PR
#    Currently uses Claude Code in headless mode; adaptable to other AI providers
#    Resumable — picks up where it left off if interrupted
./scripts/run_batch_assess.sh

# 4. Test coverage at each PR's initial submission and merge point
#    Only for repos with a pytest-based test suite
python scripts/calculate_coverage.py
```

Steps 1 and 4 are fast and free — just git operations and local test runs. Step 2 is rate-limited by the GitHub API but only needs to do a full fetch once; after that, it incrementally fetches only PRs with new activity. Step 3 is the AI analysis pass — it reads the stored PR data from the database, generates structured assessments, and writes them back. Each PR is committed individually, so progress is never lost.

All four steps are incremental and idempotent, so they can be run on a cron schedule to keep the index current as the project evolves.

## What's in the database

- **git_commits** — full commit history from local clones (authors, file changes, insertions/deletions)
- **github_prs** — PR metadata, bodies, review comments, issue comments (complete API data as JSON)
- **github_issues** — issue metadata and bodies
- **pr_assessments** — AI-generated analysis per PR: tier, complexity, risk, narrative summary, per-participant contributions, lessons learned, outcome assessment, cross-references to related PRs/issues, test coverage evolution from initial submission to merge
- **ai_digests** — cached AI outputs with content hashes, so unchanged data is never re-analyzed
- **analysis_runs** — when each scan or indexing job ran

Contributor roles and expertise are not manually assigned — they emerge from AI analysis of the indexed data.

## Planned

- **Semantic search** — embed all AI-digested content into a vector database (ChromaDB) for similarity search across the full project history. "What did we discuss about SeedXOR?" returns relevant PR discussions, Telegram threads, and decision records.
- **Contributor profiles** — AI-generated characterizations of each contributor's activity, expertise areas, engagement trajectory, and review style, refreshed as new data is indexed.
- **Telegram ingestion** — ingest group chat history and extract decisions, action items, and topic-level summaries.
- **Translation completeness tracking** — parse `.po` files to track per-language translation progress over time, with both string-based and word-weighted completion metrics.

## License

MIT
