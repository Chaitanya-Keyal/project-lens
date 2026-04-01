You are running as an unattended batch job for project-lens. Your task is to analyze Telegram group chat messages from the SeedSigner Devs group, extract substantive project discussions, and discard noise.

## Setup
Working directory: /home/kdmukai/dev/tools/project-lens
Python venv: source .venv/bin/activate

## Step 0: Load contributor context

Run this to get contributor profiles and top Telegram senders. Use this to understand who the key contributors are and to map Telegram display names to GitHub identities.

```bash
source .venv/bin/activate && DEBUG=false python -c "
import asyncio
from backend.database import async_session
from backend.models import ContributorProfile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from backend.config import settings
from backend.models import TelegramMsg

tg_engine = create_async_engine(settings.telegram_db_url)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)

async def show():
    async with async_session() as s:
        result = await s.execute(
            select(ContributorProfile)
            .order_by(ContributorProfile.last_active.desc())
        )
        print('=== Contributor Profiles (from git + GitHub) ===')
        for p in result.scalars():
            s_ = p.stats
            total = s_.get('merge_commits',0) + s_.get('prs_merged',0) + s_.get('total_review_engagement',0)
            if total < 5:
                continue
            mt = s_.get('merge_tier', 'none')
            pt = s_.get('pr_tier', 'none')
            rt = s_.get('review_tier', 'none')
            names = ', '.join(n for n in p.display_names if n != p.github_login)
            print(f'{p.github_login} (names: {names}) — merges:{mt} shipped:{pt} reviews:{rt}')

    async with tg_session() as s:
        result = await s.execute(
            select(TelegramMsg.from_user, func.count(TelegramMsg.id))
            .where(TelegramMsg.from_user.is_not(None))
            .group_by(TelegramMsg.from_user)
            .order_by(func.count(TelegramMsg.id).desc())
            .limit(25)
        )
        print()
        print('=== Top Telegram Senders ===')
        for name, count in result.all():
            print(f'{name}: {count} messages')

asyncio.run(show())
"
```

Each contributor profile has three tier scores derived from the project's own statistical distribution:
- **merge tier** (high/moderate/low/none) — who merges other people's PRs
- **shipped tier** (high/moderate/low/none) — whose PRs get accepted
- **review tier** (high/moderate/low/none) — who actively reviews and comments on others' PRs

Use the contributor `names` field and the Telegram sender list to map Telegram display names to GitHub identities (e.g., "Nick Klockenga" → newtonick). When a contributor with high/moderate tiers reacts to or endorses a proposal, that carries more weight than a low/none-tier participant.

## Step 1: Fetch the next unprocessed batch

Run this to get the next batch of unprocessed messages (one week at a time):

```bash
source .venv/bin/activate && DEBUG=false python -c "
import asyncio, json
from datetime import timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from backend.config import settings
from backend.models import TelegramMsg

tg_engine = create_async_engine(settings.telegram_db_url)
tg_session = async_sessionmaker(tg_engine, class_=AsyncSession, expire_on_commit=False)

async def dump():
    async with tg_session() as s:
        # Find earliest unprocessed message date
        result = await s.execute(
            select(func.min(TelegramMsg.date))
            .where(TelegramMsg.is_processed == False)
        )
        earliest = result.scalar()
        if not earliest:
            print('ALL_PROCESSED')
            return

        # Get one week's worth from that date
        batch_end = earliest + timedelta(days=7)

        result = await s.execute(
            select(TelegramMsg)
            .where(TelegramMsg.is_processed == False)
            .where(TelegramMsg.date >= earliest)
            .where(TelegramMsg.date < batch_end)
            .order_by(TelegramMsg.date)
        )
        msgs = result.scalars().all()

        # Find replies that point to messages outside this batch window.
        # Include those originals as context so the AI can follow the thread.
        batch_ids = {m.message_id for m in msgs}
        reply_targets = {m.reply_to_message_id for m in msgs if m.reply_to_message_id and m.reply_to_message_id not in batch_ids}
        context_msgs = []
        if reply_targets:
            result = await s.execute(
                select(TelegramMsg)
                .where(TelegramMsg.chat_id == msgs[0].chat_id)
                .where(TelegramMsg.message_id.in_(list(reply_targets)))
            )
            context_msgs = result.scalars().all()

        # Count remaining
        result = await s.execute(
            select(func.count(TelegramMsg.id))
            .where(TelegramMsg.is_processed == False)
        )
        remaining = result.scalar()

        batch_start = str(earliest)[:10]
        batch_end_str = str(batch_end)[:10]
        print(f'Batch: {batch_start} to {batch_end_str} ({len(msgs)} messages)')
        print(f'Remaining unprocessed: {remaining}')
        print(f'Chat ID: {msgs[0].chat_id if msgs else 0}')
        print()

        if context_msgs:
            print(f'--- CONTEXT: {len(context_msgs)} referenced message(s) from outside this batch ---')
            for m in sorted(context_msgs, key=lambda x: x.message_id):
                text = (m.text or '').replace(chr(10), ' ')[:300]
                print(f'[{m.message_id}] {str(m.date)[:16]} {m.from_user or \"?\"}: {text}')
            print(f'--- END CONTEXT ---')
            print()

        for m in msgs:
            reply = f' [reply to {m.reply_to_message_id}]' if m.reply_to_message_id else ''
            media = f' [{m.message_type}]' if m.message_type != 'text' else ''
            text = (m.text or '').replace(chr(10), ' ')[:300]
            reactions = ''
            if m.reactions:
                parts = []
                for r in m.reactions:
                    names = ', '.join(r.get('from', []))
                    parts.append(f'{r[\"emoji\"]}x{r[\"count\"]}({names})')
                reactions = f' | reactions: {\" \".join(parts)}'
            print(f'[{m.message_id}] {str(m.date)[:16]} {m.from_user or \"?\"}{reply}{media}: {text}{reactions}')

asyncio.run(dump())
"
```

If the output says `ALL_PROCESSED`, you're done — report that and stop.

## Step 2: Analyze the batch

Read through the messages and identify **substantive discussion threads**. Group related messages into threads by topic. You are looking for two tiers of content:

### Tier 1: PR-linked / actionable
Discussions that directly reference or relate to specific PRs, issues, code changes, bugs, releases, or testing results. These often include:
- Implementation approach discussions
- Review discussion that happened in Telegram instead of GitHub
- Bug reports or troubleshooting
- Release coordination
- Action items assigned to specific people

### Tier 2: Broader project context
Discussions about the project at a higher level that don't map to a specific PR but inform project direction. These include:
- Security model analysis: threat vectors, attack scenarios, trust boundaries
- Architecture discussions: platform strategy, hardware choices, protocol decisions
- Ecosystem context: how SeedSigner relates to other bitcoin tools, standards, BIPs
- Community/governance: contribution policy, maintainer philosophy, project direction

These broader discussions are valuable knowledge base items — they capture the reasoning and threat analysis behind specific choices that may come later.

### What to discard
- Greetings, banter, jokes, memes, off-topic chat
- One-word reactions or simple acknowledgments ("ok", "nice", "👍")
- Bot messages, spam, or self-promotion
- Personal conversations unrelated to the project
- Messages that are purely logistical without technical substance ("I'll be on later")

When in doubt about whether something is substantive, consider: would a new contributor or maintainer benefit from knowing this was discussed? If yes, keep it.

## Step 3: Produce structured output

Fill in this exact JSON skeleton for each batch. Do not add, remove, or rename any fields.

```json
{
  "batch_start": "YYYY-MM-DD",
  "batch_end": "YYYY-MM-DD",
  "chat_id": 0,
  "threads": [
    {
      "topic": "Short descriptive title",
      "category": "pr_linked|security|architecture|ecosystem|process|roadmap",
      "summary": "Narrative summary naming participants. Self-contained for embedding.",
      "participants": ["Display Name", "Display Name"],
      "pr_references": ["Owner/Repo#123"],
      "issue_references": ["Owner/Repo#456"],
      "key_points": ["Concise bullet point"],
      "per_person_takes": [
        {"person": "Display Name", "gist": "Natural-language summary of their position"}
      ],
      "message_ids": [100, 101, 102]
    }
  ],
  "processed_message_ids": [100, 101, 102, 103, 104],
  "stats": {
    "total_messages": 0,
    "substantive_messages": 0,
    "discarded_messages": 0
  }
}
```

**Schema rules — the validator will reject output that violates these:**
- Exactly these top-level keys: `batch_start`, `batch_end`, `chat_id`, `threads`, `processed_message_ids`, `stats`. No extra keys.
- `processed_message_ids`: flat array of integers — every message ID in the batch (kept AND discarded). Not a dict.
- `per_person_takes`: array of `{"person": str, "gist": str}` objects. Not a dict. Optional — omit the key entirely for threads where individual positions aren't interesting.
- `stats` values: all integers.
- `category`: exactly one of `pr_linked`, `security`, `architecture`, `ecosystem`, `process`, `roadmap`.
- `message_ids`: array of integers — only the messages that contributed to this thread.
- `participants`: array of strings — use Telegram display names as they appear in messages.

**Content guidance:**
- `summary`: write for embedding — self-contained, names participants, captures what was discussed and why it matters.
- `pr_references` / `issue_references`: format `Owner/Repo#123`. Only explicit references from the messages.
- `per_person_takes.gist`: natural-language sentence, not a label — "what would I tell someone who asked what this person thought?"
- Reactions: when reactions from high/moderate-tier contributors indicate endorsement or skepticism on a substantive point, note it in summary or key_points. Don't catalog every reaction.

## Step 4: Write the digest

Write the JSON to `/tmp/telegram_digest.json`, then run:

```bash
source .venv/bin/activate && DEBUG=false python scripts/write_telegram_digest.py --file /tmp/telegram_digest.json
```

The write script validates the JSON schema before writing. If it prints validation errors, read each error, fix the JSON file accordingly, and re-run the write script. Repeat until it succeeds.

## Step 5: Report and exit

After writing the batch, report what was processed and exit. The outer loop script will relaunch with a fresh context for the next batch.

```
Batch 2023-01-01 to 2023-01-08: N threads extracted, M messages processed
```

## Guidelines

- **Be aggressive about discarding noise.** A typical dev group chat is 50-80% banter, greetings, and off-topic discussion. That's fine — only the signal matters.
- **Don't over-thread.** If 3 messages mention the same topic in passing, that's not a thread — it's chatter. A thread is a sustained exchange where something substantive is discussed, debated, or decided.
- **Preserve attribution.** When summarizing, name who raised a concern, who proposed a solution, who set direction. This matters for understanding the project's dynamics.
- **Link to PRs/issues when referenced.** Messages often mention PR numbers or issue numbers — capture these as references.
- **Security discussions are high-value.** Even if they don't result in a PR, security analysis and threat modeling capture important institutional knowledge about why the project makes certain design choices.
- **Quiet weeks are fine.** Some weeks will produce zero threads — that's expected. Just mark all messages as processed and move on.
- **Write summaries for embedding.** The summary field will be embedded for semantic search. It should be self-contained — someone reading just the summary (without the original messages) should understand what was discussed and why it matters.
