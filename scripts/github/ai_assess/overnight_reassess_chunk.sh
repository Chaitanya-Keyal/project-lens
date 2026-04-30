#!/bin/bash
# Overnight issue-reassessment chunk. Reassesses up to N oldest open-issue
# assessments per invocation. Writes a campaign-start marker on first run,
# then self-removes the temporary cron entries once every open-issue
# assessment's updated_at is newer than the campaign start.
#
# Usage:
#   bash overnight_reassess_chunk.sh [N]   # N defaults to 30
set -e
# Cron has a minimal PATH — ensure claude is reachable (mirrors github-pipeline.sh)
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/../../.."
source .venv/bin/activate

LIMIT="${1:-30}"
MARKER="PROJECT_LENS_ISSUE_REASSESS_OVERNIGHT"
CAMPAIGN_FILE="var/overnight_reassess_campaign_start"
DB="$(PYTHONPATH=src python -c 'from project_lens.settings import settings; u=settings.database_url.replace("sqlite+aiosqlite:///",""); print(u)')"

mkdir -p var

if [ ! -f "$CAMPAIGN_FILE" ]; then
    date -u '+%Y-%m-%d %H:%M:%S' > "$CAMPAIGN_FILE"
    echo "[$(date)] Campaign started; marker written to $CAMPAIGN_FILE"
fi
CAMPAIGN_START=$(cat "$CAMPAIGN_FILE")

OLDEST=$(sqlite3 "$DB" "SELECT MIN(ia.updated_at) FROM issue_assessments ia
    JOIN github_issues i ON ia.repo=i.repo AND ia.issue_number=i.number
    WHERE i.state='open';")

if [ -n "$OLDEST" ] && [ "$OLDEST" \> "$CAMPAIGN_START" ]; then
    echo "[$(date)] Campaign complete — oldest open-issue assessment ($OLDEST) is newer than campaign start ($CAMPAIGN_START). Removing cron entries."
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    rm -f "$CAMPAIGN_FILE"
    exit 0
fi

echo "[$(date)] Running chunk — LIMIT=$LIMIT, campaign_start=$CAMPAIGN_START, oldest=$OLDEST"
PYTHONPATH=src DEBUG=false python scripts/github/ai_assess/reassess_open_issues.py --limit "$LIMIT"
bash scripts/github/ai_assess/batch_assess_issues.sh
echo "[$(date)] Chunk complete."
