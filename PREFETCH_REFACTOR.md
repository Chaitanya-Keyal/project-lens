# Refactor: Prefetch data in shell scripts instead of AI tool calls

## Status (2026-04-06)

**Complete.** All four batch pipelines now use the prefetch pattern.

### Implemented

- Thread -> message linking pipeline: `fetch_unlinked_threads.py`, `link_threads_prompt.md`, `write_thread_links.py`, `link_threads.sh` — reference implementation
- PR assessment pipeline: `fetch_unassessed_prs.py`, `batch_assess_prompt.md`, `batch_assess.sh`
- Issue assessment pipeline: `fetch_unassessed_issues.py`, `batch_assess_issues_prompt.md`, `batch_assess_issues.sh`
- Telegram digest pipeline: `fetch_unprocessed_messages.py`, `telegram_digest_prompt.md`, `telegram_digest.sh`
- Shared `scripts/shared/fetch_contributor_context.py` — reusable contributor profile + merge authority + telegram sender fetch, imported by all prefetch scripts
- All prompts updated: `source .venv/bin/activate` removed from bash code blocks, replaced with "Do NOT prefix" instruction
- `link_threads_prompt.md` has the correct "Do NOT prefix with `source .venv/bin/activate`" instruction
- Telegram digest prompt updated to include `first_message_id` in thread output schema
- `embed_digests.py` exposes `first_message_id` in ChromaDB metadata for telegram threads

## Prefetch pattern

The shell script does three things per iteration:

1. **Prefetch**: Run a Python fetch script, capture its output. Check for a sentinel (`ALL_PROCESSED`, `ALL_LINKED`, `ALL_ASSESSED`) — if found, stop the loop without launching the AI.
2. **Build prompt**: Write the prompt template + prefetched data to a temp file. The data goes under a `## Prefetched Data` section appended to the template.
3. **Launch AI**: Pipe the temp file to `claude -p` via stdin: `cat "$prompt_file" | claude -p --max-turns N`

The prompt tells the AI the data is already provided and to skip straight to analysis. The AI only makes tool calls for **writes** (creating JSON output, running the write/validate scripts).

### Reference implementation

`scripts/telegram/ai_assess/link_threads.sh` implements this pattern correctly:

```bash
prompt_file=$(mktemp /tmp/link_threads_prompt.XXXXXX.md)
trap "rm -f $prompt_file" EXIT

while true; do
    prefetch=$(PYTHONPATH=src DEBUG=false python scripts/.../fetch_unlinked_threads.py 2>&1)

    if echo "$prefetch" | head -1 | grep -q "ALL_LINKED"; then
        echo "Nothing left to process."
        break
    fi

    cat scripts/.../link_threads_prompt.md > "$prompt_file"
    echo "" >> "$prompt_file"
    echo "## Prefetched Data" >> "$prompt_file"
    echo "" >> "$prompt_file"
    echo "$prefetch" >> "$prompt_file"

    cat "$prompt_file" | claude -p --permission-mode bypassPermissions --max-turns N || true
done
```

### Why the temp file

The prefetch output needs to be read twice: once to check for the sentinel (before deciding whether to launch the AI), and once to include in the prompt. A shell variable captures it for the sentinel check; the temp file holds it for piping. Piping via stdin avoids the shell `ARG_MAX` limit (~2MB) that could be hit if the prompt were passed as a command-line argument to `claude -p`.

## Key principles

- The AI should only make tool calls for **writes** (writing JSON, running the write/validate scripts). All **reads** should be prefetched by the shell script.
- The shell script is responsible for the fetch -> check -> launch loop. The AI prompt is responsible for analysis -> output -> write.
- If the prefetched data indicates there's nothing to process, the shell script should detect this and skip the AI invocation entirely.
- **Pipe the prompt via stdin** (`cat "$prompt_file" | claude -p`) rather than passing it as a command-line argument. Large batches can exceed the shell's `ARG_MAX` limit (~2MB).
- **Bash commands in prompts must match the global allow list.** The global `~/.claude/settings.json` auto-approves `Bash(python *)` but NOT `Bash(source *)`. Commands in prompts should start with `python` or `PYTHONPATH=src python`, never `source .venv/bin/activate && python ...`. The shell script already activates the venv before launching `claude -p` — the child process inherits it.
- The `check-autonomous.sh` PreToolUse hook falls through with no opinion for unrecognized commands (like `source ...`), which then fails the allow list check. The hook explicitly approves git read-only commands and gates git/gh writes via flag files, but doesn't handle Python script execution — that relies solely on the `Bash(python *)` allow pattern.

## Fetch script architecture

All prefetch scripts share contributor context via `scripts/shared/fetch_contributor_context.py`:

```
scripts/shared/fetch_contributor_context.py      (shared: profiles + merge authorities + telegram senders)
scripts/github/ai_assess/fetch_unassessed_prs.py (imports shared, adds PR batch data)
scripts/github/ai_assess/fetch_unassessed_issues.py (imports shared, adds issue batch data)
scripts/telegram/ai_assess/fetch_unprocessed_messages.py (imports shared, adds message batch data)
scripts/telegram/ai_assess/fetch_unlinked_threads.py (standalone, thread linking only)
```

Each fetch script:
- Prints a sentinel (`ALL_ASSESSED`, `ALL_PROCESSED`, `ALL_LINKED`) when nothing remains
- Otherwise prints contributor context + batch data as formatted text
- Is invoked with `PYTHONPATH=src` so project_lens imports work
