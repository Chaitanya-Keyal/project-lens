from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from project_lens.models.base import Base


class IssueAssessment(Base):
    """AI-generated analysis per issue. Populated by Claude Code batch job."""
    __tablename__ = "issue_assessments"
    __table_args__ = (UniqueConstraint("repo", "issue_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    complexity: Mapped[str | None] = mapped_column(String, nullable=True)
    impact: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String, nullable=True)
    # Discussion state: active|stale|resolved|wont_fix|duplicate
    discussion_state: Mapped[str | None] = mapped_column(String, nullable=True)
    # Why stale (if applicable): neglected|no_traction|waiting_on_author|waiting_on_maintainer|null
    stale_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Per-participant breakdown: [{github_username, role, contributions: [{type, detail}]}]
    participant_contributions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Key positions/decisions from discussion: [{user, position}]
    discussion_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Structured links: [{type, target, note}]
    cross_references: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Suggested labels: ["bug", "enhancement", ...]
    labels_suggested: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
