from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GithubPR(Base):
    __tablename__ = "github_prs"
    __table_args__ = (UniqueConstraint("repo", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    review_state: Mapped[str | None] = mapped_column(String, nullable=True)
    mergeable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    diff_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    merged_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Review/comment data fetched separately (expensive, one per PR)
    reviews_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    review_comments_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    issue_comments_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
