from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from project_lens.models.base import Base


class PRAssessment(Base):
    """AI-generated analysis per PR. Populated by Claude Code cron in Phase 2."""
    __tablename__ = "pr_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    merge_target: Mapped[str | None] = mapped_column(String, nullable=True)
    reviews: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tested_on_hardware: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    complexity: Mapped[str | None] = mapped_column(String, nullable=True)
    risk: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String, nullable=True)
    # Per-participant breakdown: [{github_username, role, contributions: [{type, detail}]}]
    participant_contributions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Lessons/principles extracted from review: [{category, principle, detail, source}]
    lessons_learned: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # PR outcome: {category, impact, review_improved_outcome, review_improvement}
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Structured links: [{type, target, note}]
    cross_references: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Test coverage at key commit points:
    # [{commit, label, date, overall, new_code, files_untested, ...}]
    coverage_evolution: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Per-person open items: {username: {role, open_items, summary, last_activity_at}}
    pending_actions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pending_actions_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Merge readiness: not_ready|possibly_ready|merge_ready
    merge_readiness: Mapped[str | None] = mapped_column(String, nullable=True)
    merge_readiness_rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    # Per-reviewer sentiment: [{reviewer, sentiment, needs}]
    reviewer_sentiment: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Why a PR is stale: neglected|indecisive|soft_rejected|null
    stale_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
