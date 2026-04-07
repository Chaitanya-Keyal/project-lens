#!/bin/bash
# Run Telegram digest in a loop — one batch per claude -p invocation.
# Fresh context each time, no wasted tokens on accumulated history.
# Each run picks up where the last left off via is_processed flags.
#
# Usage: ./scripts/telegram/ai_assess/telegram_digest.sh

set -e
cd "$(dirname "$0")/../../.."
source .venv/bin/activate

prompt_file=$(mktemp /tmp/telegram_digest_prompt.XXXXXX.md)
trap "rm -f $prompt_file" EXIT

batch_count=0

while true; do
    prefetch=$(PYTHONPATH=src DEBUG=false python scripts/telegram/ai_assess/fetch_unprocessed_messages.py 2>&1) || {
        echo "Fetch failed: $prefetch"
        exit 1
    }

    if echo "$prefetch" | head -1 | grep -q "ALL_PROCESSED"; then
        echo "All messages processed after $batch_count batches."
        break
    fi

    batch_count=$((batch_count + 1))
    echo "=== Batch $batch_count ==="

    # Build prompt: template + prefetched data
    cat scripts/telegram/ai_assess/telegram_digest_prompt.md > "$prompt_file"
    echo "" >> "$prompt_file"
    echo "## Prefetched Data" >> "$prompt_file"
    echo "" >> "$prompt_file"
    echo "$prefetch" >> "$prompt_file"

    cat "$prompt_file" | claude -p \
      --permission-mode bypassPermissions \
      --max-turns 20 \
      || true
done
