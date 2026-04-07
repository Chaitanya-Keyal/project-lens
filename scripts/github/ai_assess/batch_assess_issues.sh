#!/bin/bash
# Run issue assessment in a loop — one batch per claude -p invocation.
# Fresh context each time, no wasted tokens on accumulated history.
# Each run picks up where the last left off (unassessed issues query).
#
# Usage: ./scripts/github/ai_assess/batch_assess_issues.sh
#
# Logs to var/batch_assess_issues.log

set -e
cd "$(dirname "$0")/../../.."
source .venv/bin/activate

mkdir -p var

prompt_file=$(mktemp /tmp/batch_assess_issues_prompt.XXXXXX.md)
trap "rm -f $prompt_file" EXIT

batch_count=0

while true; do
    prefetch=$(PYTHONPATH=src DEBUG=false python scripts/github/ai_assess/fetch_unassessed_issues.py 2>&1) || {
        echo "Fetch failed: $prefetch"
        exit 1
    }

    if echo "$prefetch" | head -1 | grep -q "ALL_ASSESSED"; then
        echo "$(date): All issues assessed after $batch_count batches." >> var/batch_assess_issues.log
        echo "All issues assessed after $batch_count batches."
        break
    fi

    batch_count=$((batch_count + 1))
    echo "$(date): Starting batch $batch_count" >> var/batch_assess_issues.log

    # Build prompt: template + prefetched data
    cat scripts/github/ai_assess/batch_assess_issues_prompt.md > "$prompt_file"
    echo "" >> "$prompt_file"
    echo "## Prefetched Data" >> "$prompt_file"
    echo "" >> "$prompt_file"
    echo "$prefetch" >> "$prompt_file"

    cat "$prompt_file" | claude -p \
      --permission-mode bypassPermissions \
      --max-turns 50 \
      >> var/batch_assess_issues.log 2>&1 || true

    echo "$(date): Batch $batch_count complete" >> var/batch_assess_issues.log
done
