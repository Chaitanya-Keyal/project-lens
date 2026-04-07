#!/usr/bin/env python3
"""Validate issue assessment JSON against the expected schema.

Used by write_issue_assessment.py as a gate before writing to the database.
Can also be run standalone for testing.

Usage:
    python scripts/github/ai_assess/validate_issue_assessment.py --file /tmp/issue_batch_assessments.json
"""
import json
import sys
from pathlib import Path

CORE_FIELDS = {
    "repo": str,
    "issue_number": int,
    "tier": int,
    "category": str,
    "complexity": str,
    "impact": str,
    "summary": str,
    "recommendation": str,
    "discussion_state": str,
}

CORE_STRUCTURED = {"cross_references", "participant_contributions", "discussion_positions", "labels_suggested"}

NULLABLE_FIELDS = {"stale_reason"}

ALL_ALLOWED = set(CORE_FIELDS.keys()) | CORE_STRUCTURED | NULLABLE_FIELDS

VALID_CATEGORY = {"bug", "feature_request", "question", "discussion", "task", "support"}
VALID_COMPLEXITY = {"trivial", "low", "medium", "high"}
VALID_IMPACT = {"low", "medium", "high"}
VALID_DISCUSSION_STATE = {"active", "stale", "resolved", "wont_fix", "duplicate"}
VALID_STALE_REASON = {"neglected", "no_traction", "waiting_on_author", "waiting_on_maintainer", None}
VALID_XREF_TYPE = {"related_pr", "fixed_by", "duplicate_of", "discussed_in", "depends_on", "spawned"}


def validate_one(data: dict) -> list[str]:
    """Validate a single issue assessment. Returns list of error messages."""
    errors = []
    prefix = f"Issue #{data.get('issue_number', '?')}"

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
    if data.get("tier") is not None and data["tier"] not in (0, 1, 2, 3):
        errors.append(f"{prefix}: tier must be 0-3, got {data['tier']}")
    if data.get("category") and data["category"] not in VALID_CATEGORY:
        errors.append(f"{prefix}: category '{data['category']}' not in {VALID_CATEGORY}")
    if data.get("complexity") and data["complexity"] not in VALID_COMPLEXITY:
        errors.append(f"{prefix}: complexity '{data['complexity']}' not in {VALID_COMPLEXITY}")
    if data.get("impact") and data["impact"] not in VALID_IMPACT:
        errors.append(f"{prefix}: impact '{data['impact']}' not in {VALID_IMPACT}")
    if data.get("discussion_state") and data["discussion_state"] not in VALID_DISCUSSION_STATE:
        errors.append(f"{prefix}: discussion_state '{data['discussion_state']}' not in {VALID_DISCUSSION_STATE}")

    # stale_reason: required when stale, must be null otherwise
    sr = data.get("stale_reason")
    ds = data.get("discussion_state")
    if sr is not None and sr not in VALID_STALE_REASON:
        errors.append(f"{prefix}: stale_reason '{sr}' not in {VALID_STALE_REASON}")
    if ds == "stale" and sr is None:
        errors.append(f"{prefix}: stale_reason is required when discussion_state is 'stale'")
    if ds and ds != "stale" and sr is not None:
        errors.append(f"{prefix}: stale_reason must be null when discussion_state is '{ds}'")

    # cross_references
    xrefs = data.get("cross_references")
    if xrefs is not None:
        if not isinstance(xrefs, list):
            errors.append(f"{prefix}: cross_references must be an array")
        else:
            for i, xref in enumerate(xrefs):
                if not isinstance(xref, dict):
                    errors.append(f"{prefix}: cross_references[{i}] must be an object")
                else:
                    xtype = xref.get("type")
                    if xtype and xtype not in VALID_XREF_TYPE:
                        errors.append(f"{prefix}: cross_references[{i}].type '{xtype}' not in {VALID_XREF_TYPE}")

    # participant_contributions
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

    # discussion_positions
    dp = data.get("discussion_positions")
    if dp is not None:
        if not isinstance(dp, list):
            errors.append(f"{prefix}: discussion_positions must be an array")
        else:
            for i, entry in enumerate(dp):
                if not isinstance(entry, dict):
                    errors.append(f"{prefix}: discussion_positions[{i}] must be an object")
                elif not all(k in entry for k in ("user", "position")):
                    errors.append(f"{prefix}: discussion_positions[{i}] must have 'user' and 'position'")

    # labels_suggested
    ls = data.get("labels_suggested")
    if ls is not None:
        if not isinstance(ls, list):
            errors.append(f"{prefix}: labels_suggested must be an array")
        elif not all(isinstance(l, str) for l in ls):
            errors.append(f"{prefix}: labels_suggested must be an array of strings")

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
        print("Usage: validate_issue_assessment.py --file <path>")
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
