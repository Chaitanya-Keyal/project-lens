from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
