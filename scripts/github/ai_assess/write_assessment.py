#!/usr/bin/env python3
"""Write a PR assessment to the database.

Called by Claude Code cron jobs after AI analysis.
Writes to both pr_assessments and ai_digests tables.

Usage:
    python scripts/write_assessment.py --json '<json>'
    python scripts/write_assessment.py --file assessments.json
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
from project_lens.models import Base, PRAssessment, AIDigest


async def write_assessment(data: dict):
    """Write a single PR assessment."""
    repo = data["repo"]
    pr_number = data["pr_number"]
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Upsert pr_assessments
        result = await session.execute(
            select(PRAssessment).where(
                PRAssessment.repo == repo,
                PRAssessment.pr_number == pr_number,
            )
        )
        existing = result.scalar_one_or_none()

        fields = {
            "tier": data.get("tier"),
            "merge_target": data.get("merge_target"),
            "reviews": data.get("reviews"),
            "tested_on_hardware": data.get("tested_on_hardware"),
            "complexity": data.get("complexity"),
            "risk": data.get("risk"),
            "summary": data.get("summary"),
            "recommendation": data.get("recommendation"),
            "participant_contributions": data.get("participant_contributions"),
            "lessons_learned": data.get("lessons_learned"),
            "outcome": data.get("outcome"),
            "cross_references": data.get("cross_references"),
            "pending_actions": data.get("pending_actions"),
            "pending_actions_updated_at": now if data.get("pending_actions") else None,
            "merge_readiness": data.get("merge_readiness"),
            "merge_readiness_rationale": data.get("merge_readiness_rationale"),
            "reviewer_sentiment": data.get("reviewer_sentiment"),
            "stale_reason": data.get("stale_reason"),
        }

        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.updated_at = now
        else:
            assessment = PRAssessment(repo=repo, pr_number=pr_number, **fields)
            session.add(assessment)

        # Upsert ai_digests (the cached AI output)
        digest_text = data.get("summary", "") + "\n\n" + data.get("recommendation", "")
        content_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()

        result = await session.execute(
            select(AIDigest).where(
                AIDigest.source_type == "github_pr",
                AIDigest.source_ref == f"{repo}#{pr_number}",
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
                source_type="github_pr",
                source_ref=f"{repo}#{pr_number}",
                digest_type="assessment",
                **digest_fields,
            )
            session.add(digest)

        await session.commit()
        print(f"  Wrote assessment for PR #{pr_number}")


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
        print("Usage: write_assessment.py --json '<json>' or --file <path>")
        sys.exit(1)

    if isinstance(assessments, dict):
        assessments = [assessments]

    # Validate before writing anything
    from scripts.github.ai_assess.validate_assessment import validate
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
