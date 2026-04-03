#!/usr/bin/env python3
"""Validate PR assessment JSON against the expected schema.

Used by write_assessment.py as a gate before writing to the database.
Can also be run standalone for testing.

Usage:
    python scripts/validate_assessment.py --file /tmp/pr_batch_assessments.json
"""
import json
import sys
from pathlib import Path

# Fields present on all assessments (open, closed, merged)
CORE_FIELDS = {
    "repo": str,
    "pr_number": int,
    "tier": int,
    "merge_target": str,
    "tested_on_hardware": bool,
    "complexity": str,
    "risk": str,
    "summary": str,
    "recommendation": str,
}

# Fields that are objects/arrays with their own structure
CORE_STRUCTURED = {"outcome", "cross_references", "participant_contributions", "lessons_learned", "reviews"}

# Fields only for open PRs
OPEN_PR_FIELDS = {"pending_actions", "merge_readiness", "merge_readiness_rationale",
                  "reviewer_sentiment", "stale_reason"}

ALL_ALLOWED = set(CORE_FIELDS.keys()) | CORE_STRUCTURED | OPEN_PR_FIELDS

VALID_COMPLEXITY = {"trivial", "low", "medium", "high"}
VALID_RISK = {"low", "medium", "high"}
VALID_MERGE_READINESS = {"not_ready", "possibly_ready", "merge_ready"}
VALID_STALE_REASON = {"neglected", "indecisive", "soft_rejected", None}
VALID_OUTCOME_CATEGORY = {"feature", "bugfix", "refactor", "cleanup", "security",
                          "docs", "l10n", "performance", "testing"}
VALID_REVIEW_TYPE = {"APPROVED", "COMMENTED", "CHANGES_REQUESTED"}


def validate_one(data: dict) -> list[str]:
    """Validate a single PR assessment. Returns list of error messages."""
    errors = []
    prefix = f"PR #{data.get('pr_number', '?')}"

    # Check for extra keys
    extra = set(data.keys()) - ALL_ALLOWED
    if extra:
        errors.append(f"{prefix}: extra keys: {extra}")

    # Required core fields
    for key, expected_type in CORE_FIELDS.items():
        val = data.get(key)
        if val is None:
            errors.append(f"{prefix}: missing required field '{key}'")
        elif not isinstance(val, expected_type):
            errors.append(f"{prefix}: '{key}' must be {expected_type.__name__}, got {type(val).__name__}")

    # Enum validations
    if data.get("complexity") and data["complexity"] not in VALID_COMPLEXITY:
        errors.append(f"{prefix}: complexity '{data['complexity']}' not in {VALID_COMPLEXITY}")
    if data.get("risk") and data["risk"] not in VALID_RISK:
        errors.append(f"{prefix}: risk '{data['risk']}' not in {VALID_RISK}")
    if data.get("tier") is not None and data["tier"] not in (0, 1, 2, 3):
        errors.append(f"{prefix}: tier must be 0-3, got {data['tier']}")

    # outcome
    outcome = data.get("outcome")
    if outcome is not None:
        if not isinstance(outcome, dict):
            errors.append(f"{prefix}: outcome must be an object")
        else:
            cat = outcome.get("category")
            if cat and cat not in VALID_OUTCOME_CATEGORY:
                errors.append(f"{prefix}: outcome.category '{cat}' not in {VALID_OUTCOME_CATEGORY}")

    # participant_contributions — array of objects
    pc = data.get("participant_contributions")
    if pc is not None:
        if not isinstance(pc, list):
            errors.append(f"{prefix}: participant_contributions must be an array")
        else:
            for i, entry in enumerate(pc):
                if not isinstance(entry, dict):
                    errors.append(f"{prefix}: participant_contributions[{i}] must be an object")
                elif "github_username" not in entry:
                    errors.append(f"{prefix}: participant_contributions[{i}] missing 'github_username'")

    # reviews — array of {user, type}
    reviews = data.get("reviews")
    if reviews is not None:
        if not isinstance(reviews, list):
            errors.append(f"{prefix}: reviews must be an array")
        else:
            for i, r in enumerate(reviews):
                if not isinstance(r, dict):
                    errors.append(f"{prefix}: reviews[{i}] must be an object")
                else:
                    rt = r.get("type")
                    if rt and rt not in VALID_REVIEW_TYPE:
                        errors.append(f"{prefix}: reviews[{i}].type '{rt}' not in {VALID_REVIEW_TYPE}")

    # Open PR fields
    mr = data.get("merge_readiness")
    if mr is not None and mr not in VALID_MERGE_READINESS:
        errors.append(f"{prefix}: merge_readiness '{mr}' not in {VALID_MERGE_READINESS}")

    sr = data.get("stale_reason")
    if sr is not None and sr not in VALID_STALE_REASON:
        errors.append(f"{prefix}: stale_reason '{sr}' not in {VALID_STALE_REASON}")

    # reviewer_sentiment — array of {reviewer, sentiment, needs}
    rs = data.get("reviewer_sentiment")
    if rs is not None:
        if not isinstance(rs, list):
            errors.append(f"{prefix}: reviewer_sentiment must be an array, got {type(rs).__name__}")
        else:
            for i, entry in enumerate(rs):
                if not isinstance(entry, dict):
                    errors.append(f"{prefix}: reviewer_sentiment[{i}] must be an object")
                elif not all(k in entry for k in ("reviewer", "sentiment", "needs")):
                    errors.append(f"{prefix}: reviewer_sentiment[{i}] must have 'reviewer', 'sentiment', 'needs'")
                elif " " in (entry.get("sentiment") or ""):
                    errors.append(f"{prefix}: reviewer_sentiment[{i}].sentiment must be one word, got '{entry['sentiment']}'")

    # pending_actions — dict keyed by username
    pa = data.get("pending_actions")
    if pa is not None:
        if not isinstance(pa, dict):
            errors.append(f"{prefix}: pending_actions must be a dict, got {type(pa).__name__}")
        else:
            for username, entry in pa.items():
                if not isinstance(entry, dict):
                    errors.append(f"{prefix}: pending_actions['{username}'] must be an object")
                    continue
                oi = entry.get("open_items")
                if oi is not None and not isinstance(oi, int):
                    errors.append(f"{prefix}: pending_actions['{username}'].open_items must be an integer, got {type(oi).__name__}")
                summary = entry.get("summary", "")
                if summary and not summary.lower().startswith(username.lower()):
                    errors.append(f"{prefix}: pending_actions['{username}'].summary must start with the username")

    return errors


def validate(data) -> list[str]:
    """Validate one or more assessments. Returns list of error messages."""
    if isinstance(data, dict):
        data = [data]
    errors = []
    for assessment in data:
        errors.extend(validate_one(assessment))
    return errors


def main():
    if "--file" not in sys.argv:
        print("Usage: validate_assessment.py --file <path>")
        sys.exit(1)

    idx = sys.argv.index("--file")
    with open(sys.argv[idx + 1]) as f:
        data = json.load(f)

    errors = validate(data)

    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        count = len(data) if isinstance(data, list) else 1
        print(f"VALIDATION PASSED — {count} assessment(s)")
        sys.exit(0)


if __name__ == "__main__":
    main()
