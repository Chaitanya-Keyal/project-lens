#!/bin/bash
# Run unattended PR assessment batch job via Claude Code
# Usage: ./scripts/run_batch_assess.sh
#
# Logs to var/batch_assess.log

set -e
cd /home/kdmukai/dev/tools/project-lens

mkdir -p var

PROMPT=$(cat scripts/batch_assess_prompt.md)

echo "$(date): Starting batch assessment run" >> var/batch_assess.log

claude -p \
  --permission-mode bypassPermissions \
  --max-turns 50 \
  "$PROMPT" \
  >> var/batch_assess.log 2>&1

echo "$(date): Batch assessment run complete" >> var/batch_assess.log
