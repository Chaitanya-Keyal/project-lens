You are running as an unattended batch job for project-lens. Your task is to identify which Telegram message started each thread digest.

## Instructions

Below under "Prefetched Data" you will find:
- One or more **thread digests** — each has a source_ref, topic, summary, participants, and key points.
- **Raw messages** from the same time period, filtered to thread participants and their reply context.

For each thread digest, find the **first message** that initiated the discussion described in the digest. This is the message that raised the topic — not a reply, not a reaction, but the message that started the conversation. If a thread clearly started from a reply chain, the first message is the one at the top of that chain (within the provided messages).

## Output

Write a JSON array to `/tmp/thread_links_batch.json`:

```json
[
  {
    "source_ref": "chat_1829170082/2023-01-06_to_2023-01-13/thread_0",
    "first_message_id": 86,
    "confidence": "high",
    "reasoning": "Brief explanation of why this message starts the thread"
  }
]
```

You must produce exactly one entry per thread digest. Use this command to write the file:

```bash
PYTHONPATH=src python -c "
import json
results = <your JSON array here>
with open('/tmp/thread_links_batch.json', 'w') as f:
    json.dump(results, f, indent=2)
print(f'Wrote {len(results)} thread links')
"
```

Do NOT prefix commands with `source .venv/bin/activate` — the venv is already active.

## Confidence levels

- **high**: The message clearly initiates the topic described in the digest.
- **medium**: The message is likely the start, but the thread could have started from a different message.
- **low**: Uncertain — the starting message may not be in the provided context, or multiple messages could be the start.

## Guidelines

- Match by topic and participants, not just timestamp. The earliest message from a participant isn't necessarily the thread starter.
- If a digest describes a discussion about a specific PR or technical topic, look for the message that first raises that subject.
- Context messages (marked `[context]`) are replies that participants responded to — they may be the actual thread starter even though they're from a different author.
- If you genuinely cannot identify the starting message, still produce an entry with confidence "low" and explain why in the reasoning.
