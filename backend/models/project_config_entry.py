from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ProjectConfigEntry(Base):
    """Governance settings copied from config YAML on each scan.

    The YAML file is the editable source; this table is the distribution
    mechanism so consumers (PM agent) can read from the DB directly.
    """
    __tablename__ = "project_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    value: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
