from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from project_lens.models.base import Base


class GitCommit(Base):
    __tablename__ = "git_commits"
    __table_args__ = (UniqueConstraint("repo", "hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    hash: Mapped[str] = mapped_column(String(40), nullable=False)
    author_name: Mapped[str] = mapped_column(String, nullable=False)
    author_email: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    files_changed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    insertions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    is_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
