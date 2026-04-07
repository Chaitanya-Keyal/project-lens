#!/usr/bin/env python3
"""Write thread link results to ai_digests after human review.

Reads collected AI results from a JSON file and sets first_message_id
on the matching ai_digests rows.

Usage:
    PYTHONPATH=src python scripts/telegram/ai_assess/write_thread_links.py --file var/thread_links_results.json
    PYTHONPATH=src python scripts/telegram/ai_assess/write_thread_links.py --file var/thread_links_results.json --min-confidence high
    PYTHONPATH=src python scripts/telegram/ai_assess/write_thread_links.py --file var/thread_links_results.json --dry-run
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.database import engine as main_engine, async_session as main_session
from project_lens.models import Base, AIDigest

VALID_CONFIDENCES = {"high", "medium", "low"}
CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1}


def validate(entries: list[dict]) -> list[str]:
    """Validate the results structure. Returns list of error messages."""
    errors = []
    seen_refs = set()

    for i, entry in enumerate(entries):
        prefix = f"[{i}]"

        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue

        # Required fields
        for key in ("source_ref", "first_message_id", "confidence", "reasoning"):
            if key not in entry:
                errors.append(f"{prefix} missing '{key}'")

        # Types
        sr = entry.get("source_ref")
        if sr is not None and not isinstance(sr, str):
            errors.append(f"{prefix}.source_ref must be a string")
        elif sr in seen_refs:
            errors.append(f"{prefix} duplicate source_ref: {sr}")
        elif sr:
            seen_refs.add(sr)

        fmid = entry.get("first_message_id")
        if fmid is not None and (not isinstance(fmid, int) or fmid <= 0):
            errors.append(f"{prefix}.first_message_id must be a positive integer, got {fmid}")

        conf = entry.get("confidence")
        if conf is not None and conf not in VALID_CONFIDENCES:
            errors.append(f"{prefix}.confidence '{conf}' not in {VALID_CONFIDENCES}")

        reasoning = entry.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            errors.append(f"{prefix}.reasoning must be a string")

    return errors


async def write(entries: list[dict], dry_run: bool = False):
    """Set first_message_id on matching ai_digests rows."""
    updated = 0
    not_found = 0
    confidence_counts = Counter()

    async with main_session() as session:
        for entry in entries:
            result = await session.execute(
                select(AIDigest).where(
                    AIDigest.source_type == "telegram_thread",
                    AIDigest.source_ref == entry["source_ref"],
                )
            )
            digest = result.scalar_one_or_none()

            if not digest:
                print(f"  WARNING: No digest found for {entry['source_ref']}")
                not_found += 1
                continue

            confidence_counts[entry["confidence"]] += 1

            if not dry_run:
                digest.first_message_id = entry["first_message_id"]
            updated += 1

        if not dry_run:
            await session.commit()

    action = "Would update" if dry_run else "Updated"
    print(f"\n{action} {updated} digests ({not_found} not found)")
    for conf in ("high", "medium", "low"):
        if confidence_counts[conf]:
            print(f"  {conf}: {confidence_counts[conf]}")


async def main():
    if "--file" not in sys.argv:
        print("Usage: write_thread_links.py --file <path> [--min-confidence high|medium|low] [--dry-run]")
        sys.exit(1)

    idx = sys.argv.index("--file")
    filepath = sys.argv[idx + 1]

    with open(filepath) as f:
        entries = json.load(f)

    if not isinstance(entries, list):
        print("ERROR: Expected a JSON array")
        sys.exit(1)

    print(f"Loaded {len(entries)} entries from {filepath}")

    # Validate
    errors = validate(entries)
    if errors:
        print(f"\nVALIDATION FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("Validation passed.")

    # Filter by confidence if requested
    if "--min-confidence" in sys.argv:
        idx = sys.argv.index("--min-confidence")
        min_conf = sys.argv[idx + 1]
        if min_conf not in VALID_CONFIDENCES:
            print(f"ERROR: --min-confidence must be one of {VALID_CONFIDENCES}")
            sys.exit(1)
        min_level = CONFIDENCE_ORDER[min_conf]
        before = len(entries)
        entries = [e for e in entries if CONFIDENCE_ORDER.get(e["confidence"], 0) >= min_level]
        print(f"Filtered to {len(entries)} entries (min confidence: {min_conf}, excluded {before - len(entries)})")

    if not entries:
        print("Nothing to write.")
        return

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no changes will be made.")

    # Ensure schema is up to date
    async with main_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await write(entries, dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
