from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class AIDigest(Base):
    """Persistent cache for AI-generated summaries and characterizations.

    Three-layer architecture: Raw Data → AI Digests (this table) → Embeddings.
    Digests are stored independently so they survive re-embedding, failures in
    later pipeline stages, and schema changes. Content hashing ensures we never
    re-analyze unchanged source material.

    source_type + source_ref + digest_type together identify a unique digest.
    content_hash detects when source material has changed and needs re-analysis.
    """
    __tablename__ = "ai_digests"
    __table_args__ = (
        UniqueConstraint("source_type", "source_ref", "digest_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str] = mapped_column(String, nullable=False)
    digest_type: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    digest_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
