from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ContributorProfile(Base):
    """Contributor profile derived from git history and GitHub activity.

    Profiles are computed by scripts/extract_contributors.py from observable
    data — not manually configured. The stats dict contains raw counts;
    consumers (AI prompts, PM dashboards) interpret significance from context.
    """
    __tablename__ = "contributor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_login: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    first_active: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_active: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
