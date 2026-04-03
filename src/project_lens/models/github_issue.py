from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from project_lens.models.base import Base


class GithubIssue(Base):
    __tablename__ = "github_issues"
    __table_args__ = (UniqueConstraint("repo", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at_gh: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
