#!/bin/bash
# GitHub data pipeline: scan → contributors → assess → embed
#
# Runs the full GitHub ingestion and assessment pipeline in order.
# Uses set -e: if any step fails, the pipeline stops immediately.
# Downstream steps depend on upstream data — running assessments on
# incomplete data would be worse than skipping them.
#
# Usage: ./scripts/pipelines/github-pipeline.sh /path/to/config-repo
# Logs:  <config-repo>/var/logs/github-pipeline.log

set -e

# Prevent overlapping runs (hourly cron may overlap a slow run)
LOCK_FILE="/tmp/project-lens-github-pipeline.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date): Pipeline already running, skipping." >> "${1:-/dev/null}/var/logs/github-pipeline.log" 2>/dev/null
    exit 0
fi

if [ -z "$1" ]; then
    echo "Usage: $0 <config-dir>"
    echo "  config-dir: path to project config repo (contains config.yaml and var/)"
    exit 1
fi

CONFIG_DIR="$(cd "$1" && pwd)"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    echo "Error: $CONFIG_DIR/config.yaml not found"
    exit 1
fi

export PROJECT_CONFIG_DIR="$CONFIG_DIR"

# Cron has a minimal PATH — ensure claude and python are reachable
export PATH="$HOME/.local/bin:$PATH"

cd "$(dirname "$0")/../.."
source .venv/bin/activate

LOG_DIR="$CONFIG_DIR/var/logs"
LOG_FILE="$LOG_DIR/github-pipeline.log"
mkdir -p "$LOG_DIR"

# All output goes to the log file
exec >> "$LOG_FILE" 2>&1

echo ""
echo "=============================="
echo "$(date): GitHub pipeline starting ($CONFIG_DIR)"
echo "=============================="

echo "$(date): Step 1 — Indexing git history"
python scripts/github/ingest/index_git_history.py

echo "$(date): Step 2 — Scanning GitHub PRs, issues, reviews"
python scripts/github/ingest/github_scan.py

echo "$(date): Step 3 — Extracting contributor profiles"
python scripts/github/process/extract_contributors.py

echo "$(date): Step 4 — Assessing new PRs"
./scripts/github/ai_assess/batch_assess.sh

echo "$(date): Step 5 — Assessing new issues"
./scripts/github/ai_assess/batch_assess_issues.sh

echo "$(date): Step 6 — Updating embeddings"
python scripts/embed_digests.py

echo "$(date): GitHub pipeline complete"
