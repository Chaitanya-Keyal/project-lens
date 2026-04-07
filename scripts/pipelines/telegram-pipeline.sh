#!/bin/bash
# Telegram digest pipeline: assess → embed
#
# Processes accumulated Telegram messages into structured thread digests.
# Messages are processed in weekly batches (boundaries from the data itself).
# The script loops until all unprocessed weeks are caught up.
#
# Uses set -e: if the digest step fails, embeddings are skipped.
#
# Thread linking (link_threads.sh) is NOT included here — it produces
# results for human review before writing to the database. Run it
# manually after this pipeline if needed.
#
# Usage: ./scripts/pipelines/telegram-pipeline.sh /path/to/config-repo
# Logs:  <config-repo>/var/logs/telegram-pipeline.log

set -e

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
LOG_FILE="$LOG_DIR/telegram-pipeline.log"
mkdir -p "$LOG_DIR"

# All output goes to the log file
exec >> "$LOG_FILE" 2>&1

echo ""
echo "=============================="
echo "$(date): Telegram pipeline starting ($CONFIG_DIR)"
echo "=============================="

echo "$(date): Step 1 — Digesting Telegram messages"
./scripts/telegram/ai_assess/telegram_digest.sh

echo "$(date): Step 2 — Updating embeddings"
python scripts/embed_digests.py

echo "$(date): Telegram pipeline complete"
