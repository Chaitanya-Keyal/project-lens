"""Configuration for LensClient — derives all paths from a config_dir."""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class LensConfig:
    """Holds paths, engine, and session factory for a project-lens config directory."""

    def __init__(self, config_dir: str | Path):
        self.config_dir = Path(config_dir)
        if not self.config_dir.is_dir():
            raise ValueError(f"Config directory does not exist: {self.config_dir}")

        self.var_dir = self.config_dir / "var"
        self.db_path = self.var_dir / "project-lens.db"
        self.chroma_path = self.var_dir / "chroma"
        self.database_url = f"sqlite+aiosqlite:///{self.db_path}"

        # Read config from .env if present
        self.embedding_model = "BAAI/bge-large-en-v1.5"
        self.gpu_service_url = ""
        env_path = self.config_dir / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("#"):
                    continue
                if line.startswith("EMBEDDING_MODEL="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        self.embedding_model = val
                elif line.startswith("GPU_SERVICE_URL="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        self.gpu_service_url = val

        self.engine = create_async_engine(
            self.database_url,
            connect_args={"check_same_thread": False},
        )
        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False,
        )

        # Telegram messages live in a separate database (sibling telegram.db)
        self.telegram_db_path = self.var_dir / "telegram.db"
        self.telegram_db_url = f"sqlite+aiosqlite:///{self.telegram_db_path}"
        self.telegram_engine = create_async_engine(
            self.telegram_db_url,
            connect_args={"check_same_thread": False},
        )
        self.telegram_session = async_sessionmaker(
            self.telegram_engine, class_=AsyncSession, expire_on_commit=False,
        )

    async def close(self):
        """Dispose the engines and release connections."""
        await self.engine.dispose()
        await self.telegram_engine.dispose()
