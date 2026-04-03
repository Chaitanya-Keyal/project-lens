#!/usr/bin/env python3
"""Validate Telegram digest JSON against the expected schema.

Run this before write_telegram_digest.py to catch structural errors
early. Returns exit code 0 if valid, 1 if errors found. Prints
specific error messages so the AI can fix them in the same run.

Usage:
    python scripts/validate_telegram_digest.py --file /tmp/telegram_digest.json
"""
import json
import sys
from pathlib import Path

REQUIRED_TOP_KEYS = {"batch_start", "batch_end", "chat_id", "threads", "processed_message_ids", "stats"}

REQUIRED_THREAD_KEYS = {"topic", "category", "summary", "participants", "pr_references",
                        "issue_references", "key_points", "message_ids"}
OPTIONAL_THREAD_KEYS = {"per_person_takes"}

VALID_CATEGORIES = {"pr_linked", "security", "architecture", "ecosystem", "process", "roadmap"}

REQUIRED_STATS_KEYS = {"total_messages", "substantive_messages", "discarded_messages"}


def validate(data: dict) -> list[str]:
    """Validate digest structure. Returns list of error messages (empty = valid)."""
    errors = []

    # Top-level keys
    extra = set(data.keys()) - REQUIRED_TOP_KEYS
    if extra:
        errors.append(f"Extra top-level keys: {extra}. Only allowed: {REQUIRED_TOP_KEYS}")
    missing = REQUIRED_TOP_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing top-level keys: {missing}")

    # batch_start, batch_end
    for key in ("batch_start", "batch_end"):
        val = data.get(key)
        if val is not None and not isinstance(val, str):
            errors.append(f"{key} must be a string, got {type(val).__name__}")

    # chat_id
    if not isinstance(data.get("chat_id"), int):
        errors.append(f"chat_id must be an integer, got {type(data.get('chat_id')).__name__}")

    # processed_message_ids — must be flat array of ints
    pmids = data.get("processed_message_ids")
    if not isinstance(pmids, list):
        errors.append(f"processed_message_ids must be a flat array, got {type(pmids).__name__}")
    elif pmids:
        non_ints = [x for x in pmids[:5] if not isinstance(x, int)]
        if non_ints:
            errors.append(f"processed_message_ids must contain only integers, found: {non_ints}")

    # stats
    stats = data.get("stats")
    if isinstance(stats, dict):
        missing_stats = REQUIRED_STATS_KEYS - set(stats.keys())
        if missing_stats:
            errors.append(f"stats missing keys: {missing_stats}")
        for key in REQUIRED_STATS_KEYS:
            if key in stats and not isinstance(stats[key], int):
                errors.append(f"stats.{key} must be an integer, got {type(stats[key]).__name__}")
    elif stats is not None:
        errors.append(f"stats must be a dict, got {type(stats).__name__}")

    # threads
    threads = data.get("threads")
    if not isinstance(threads, list):
        errors.append(f"threads must be an array, got {type(threads).__name__}")
        return errors

    for i, thread in enumerate(threads):
        prefix = f"threads[{i}]"

        if not isinstance(thread, dict):
            errors.append(f"{prefix} must be an object")
            continue

        # Required keys
        missing_t = REQUIRED_THREAD_KEYS - set(thread.keys())
        if missing_t:
            errors.append(f"{prefix} missing keys: {missing_t}")

        extra_t = set(thread.keys()) - REQUIRED_THREAD_KEYS - OPTIONAL_THREAD_KEYS
        if extra_t:
            errors.append(f"{prefix} has extra keys: {extra_t}")

        # category
        cat = thread.get("category")
        if cat and cat not in VALID_CATEGORIES:
            errors.append(f"{prefix}.category '{cat}' not in {VALID_CATEGORIES}")

        # summary
        if not isinstance(thread.get("summary"), str):
            errors.append(f"{prefix}.summary must be a string")

        # participants — array of strings
        participants = thread.get("participants")
        if not isinstance(participants, list):
            errors.append(f"{prefix}.participants must be an array")

        # key_points — array of strings
        kps = thread.get("key_points")
        if not isinstance(kps, list):
            errors.append(f"{prefix}.key_points must be an array")

        # message_ids — array of ints
        mids = thread.get("message_ids")
        if not isinstance(mids, list):
            errors.append(f"{prefix}.message_ids must be an array")
        elif mids and not isinstance(mids[0], int):
            errors.append(f"{prefix}.message_ids must contain integers")

        # pr_references, issue_references — arrays of strings
        for ref_key in ("pr_references", "issue_references"):
            refs = thread.get(ref_key)
            if refs is not None and not isinstance(refs, list):
                errors.append(f"{prefix}.{ref_key} must be an array")

        # per_person_takes — array of {person, gist} objects
        ppt = thread.get("per_person_takes")
        if ppt is not None:
            if not isinstance(ppt, list):
                errors.append(f"{prefix}.per_person_takes must be an array, got {type(ppt).__name__}")
            else:
                for j, take in enumerate(ppt):
                    if not isinstance(take, dict):
                        errors.append(f"{prefix}.per_person_takes[{j}] must be an object")
                    elif "person" not in take or "gist" not in take:
                        errors.append(f"{prefix}.per_person_takes[{j}] must have 'person' and 'gist' keys")

    return errors


def main():
    if "--file" not in sys.argv:
        print("Usage: validate_telegram_digest.py --file <path>")
        sys.exit(1)

    idx = sys.argv.index("--file")
    path = sys.argv[idx + 1]

    with open(path) as f:
        data = json.load(f)

    errors = validate(data)

    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        threads = len(data.get("threads", []))
        pmids = len(data.get("processed_message_ids", []))
        print(f"VALIDATION PASSED — {threads} threads, {pmids} message IDs")
        sys.exit(0)


if __name__ == "__main__":
    main()
