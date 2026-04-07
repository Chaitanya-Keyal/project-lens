#!/usr/bin/env python3
"""Write an issue assessment to the database.

Called by Claude Code batch jobs after AI analysis.
Writes to both issue_assessments and ai_digests tables.

Usage:
    python scripts/github/ai_assess/write_issue_assessment.py --json '<json>'
    python scripts/github/ai_assess/write_issue_assessment.py --file assessments.json
"""
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from project_lens.database import engine, async_session
from project_lens.models import Base, IssueAssessment, AIDigest


async def write_assessment(data: dict):
    """Write a single issue assessment."""
    repo = data["repo"]
    issue_number = data["issue_number"]
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Upsert issue_assessments
        result = await session.execute(
            select(IssueAssessment).where(
                IssueAssessment.repo == repo,
                IssueAssessment.issue_number == issue_number,
            )
        )
        existing = result.scalar_one_or_none()

        fields = {
            "tier": data.get("tier"),
            "category": data.get("category"),
            "complexity": data.get("complexity"),
            "impact": data.get("impact"),
            "summary": data.get("summary"),
            "recommendation": data.get("recommendation"),
            "discussion_state": data.get("discussion_state"),
            "stale_reason": data.get("stale_reason"),
            "participant_contributions": data.get("participant_contributions"),
            "discussion_positions": data.get("discussion_positions"),
            "cross_references": data.get("cross_references"),
            "labels_suggested": data.get("labels_suggested"),
        }

        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.updated_at = now
        else:
            assessment = IssueAssessment(repo=repo, issue_number=issue_number, **fields)
            session.add(assessment)

        # Upsert ai_digests (the cached AI output)
        digest_text = data.get("summary", "") + "\n\n" + data.get("recommendation", "")
        content_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()

        result = await session.execute(
            select(AIDigest).where(
                AIDigest.source_type == "github_issue",
                AIDigest.source_ref == f"{repo}#{issue_number}",
                AIDigest.digest_type == "assessment",
            )
        )
        existing_digest = result.scalar_one_or_none()

        digest_fields = {
            "content_hash": content_hash,
            "digest_text": digest_text,
            "model": data.get("model", "claude-opus-4-6"),
            "input_tokens": data.get("input_tokens"),
            "output_tokens": data.get("output_tokens"),
        }

        if existing_digest:
            for k, v in digest_fields.items():
                setattr(existing_digest, k, v)
            existing_digest.updated_at = now
        else:
            digest = AIDigest(
                source_type="github_issue",
                source_ref=f"{repo}#{issue_number}",
                digest_type="assessment",
                **digest_fields,
            )
            session.add(digest)

        await session.commit()
        print(f"  Wrote assessment for Issue #{issue_number}")


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        raw = sys.argv[idx + 1]
        assessments = json.loads(raw)
    elif "--file" in sys.argv:
        idx = sys.argv.index("--file")
        with open(sys.argv[idx + 1]) as f:
            assessments = json.load(f)
    else:
        print("Usage: write_issue_assessment.py --json '<json>' or --file <path>")
        sys.exit(1)

    if isinstance(assessments, dict):
        assessments = [assessments]

    # Validate before writing anything
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_issue_assessment import validate
    errors = validate(assessments)
    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        print(f"\nFix the errors and re-run.")
        sys.exit(1)
    print(f"Validation passed for {len(assessments)} assessment(s).")

    for a in assessments:
        await write_assessment(a)

    print(f"Done: {len(assessments)} assessment(s) written.")


if __name__ == "__main__":
    asyncio.run(main())
