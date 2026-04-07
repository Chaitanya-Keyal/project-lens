#!/bin/bash
# Link thread digests to their first Telegram message ID.
# Collect-only: results accumulate in var/thread_links_results.json for human review.
# No database writes happen in this loop.
#
# Usage: ./scripts/telegram/ai_assess/link_threads.sh
#
# After review, apply results with:
#   PYTHONPATH=src python scripts/telegram/ai_assess/write_thread_links.py --file var/thread_links_results.json

set -e
cd "$(dirname "$0")/../../.."
source .venv/bin/activate

results_file="var/thread_links_results.json"
prompt_file=$(mktemp /tmp/link_threads_prompt.XXXXXX.md)
trap "rm -f $prompt_file" EXIT

# Initialize results file as empty JSON array if it doesn't exist
if [ ! -f "$results_file" ]; then
    echo "[]" > "$results_file"
fi

batch_count=0

while true; do
    prefetch=$(PYTHONPATH=src DEBUG=false python scripts/telegram/ai_assess/fetch_unlinked_threads.py --results-file "$results_file" 2>&1) || {
        echo "Fetch failed: $prefetch"
        exit 1
    }

    if echo "$prefetch" | head -1 | grep -q "ALL_LINKED"; then
        echo "All threads linked after $batch_count batches."
        break
    fi

    batch_count=$((batch_count + 1))
    echo "=== Batch $batch_count ==="

    # Build prompt: template + prefetched data
    cat scripts/telegram/ai_assess/link_threads_prompt.md > "$prompt_file"
    echo "" >> "$prompt_file"
    echo "## Prefetched Data" >> "$prompt_file"
    echo "" >> "$prompt_file"
    echo "$prefetch" >> "$prompt_file"

    cat "$prompt_file" | claude -p --permission-mode bypassPermissions --max-turns 5 || true

    # Append batch results to cumulative file
    if [ -f /tmp/thread_links_batch.json ]; then
        python -c "
import json
with open('$results_file') as f:
    cumulative = json.load(f)
with open('/tmp/thread_links_batch.json') as f:
    batch = json.load(f)
cumulative.extend(batch)
with open('$results_file', 'w') as f:
    json.dump(cumulative, f, indent=2)
print(f'  Appended {len(batch)} results (total: {len(cumulative)})')
"
        rm /tmp/thread_links_batch.json
    else
        echo "WARNING: No batch output file found at /tmp/thread_links_batch.json"
    fi
done

echo "Results written to $results_file"
