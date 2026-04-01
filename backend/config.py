import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings


def _find_config_dir() -> Path | None:
    """Find the project config directory.

    Priority:
      1. --config-dir command line argument
      2. DEFAULT_CONFIG_DIR from .env in tool repo (dev convenience)
    """
    # Check command line: --config-dir /path/to/config
    for i, arg in enumerate(sys.argv):
        if arg == "--config-dir" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1])

    # Check for dev default in tool repo .env
    tool_env = Path(__file__).resolve().parent.parent / ".env"
    if tool_env.exists():
        for line in tool_env.read_text().splitlines():
            line = line.strip()
            if line.startswith("DEFAULT_CONFIG_DIR=") and not line.startswith("#"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return Path(val)

    return None


def _resolve_env_file() -> str | None:
    """Find the project .env file to load from the config directory."""
    config_dir = _find_config_dir()
    if config_dir:
        env_path = config_dir / ".env"
        if env_path.exists():
            return str(env_path)

    return None


class Settings(BaseSettings):
    project_config_dir: str = ""
    database_url: str = ""
    debug: bool = False
    clones_dir: str = ""
    github_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0
    telegram_database_url: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"
    chroma_dir: str = ""

    model_config = {"env_file": _resolve_env_file(), "extra": "ignore"}

    def model_post_init(self, __context):
        """After loading, apply config-dir defaults for any unset paths."""
        # If --config-dir was passed, use it even if .env had a different value
        cli_dir = _find_config_dir()
        if cli_dir:
            self.project_config_dir = str(cli_dir)

        if not self.project_config_dir:
            raise RuntimeError(
                "PROJECT_CONFIG_DIR is required. Provide it via:\n"
                "  --config-dir /path/to/config-repo\n"
                "  or PROJECT_CONFIG_DIR env var\n"
                "  or PROJECT_CONFIG_DIR in .env"
            )

        config_dir = Path(self.project_config_dir)
        if not config_dir.is_dir():
            raise RuntimeError(f"PROJECT_CONFIG_DIR does not exist: {config_dir}")

        var_dir = config_dir / "var"

        # Derive defaults from config dir if not explicitly set
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{var_dir / 'project-lens.db'}"

        if not self.clones_dir:
            self.clones_dir = str(var_dir / "clones")

    @property
    def chroma_path(self) -> Path:
        """Resolve the ChromaDB storage directory.

        Defaults to var/chroma/ alongside the main database.
        """
        if self.chroma_dir:
            return Path(self.chroma_dir)
        # Derive from main DB path
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            db_path = Path(self.database_url[len(prefix):])
            path = db_path.parent / "chroma"
        else:
            path = Path(__file__).resolve().parent.parent / "var" / "chroma"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def telegram_db_url(self) -> str:
        """Resolve the Telegram database URL.

        If TELEGRAM_DATABASE_URL is set explicitly, use it. Otherwise derive
        from the main DATABASE_URL by placing a sibling 'telegram.db' file
        next to the main database.
        """
        if self.telegram_database_url:
            return self.telegram_database_url
        main = self.database_url
        prefix = "sqlite+aiosqlite:///"
        if main.startswith(prefix):
            db_path = Path(main[len(prefix):])
            return prefix + str(db_path.parent / "telegram.db")
        return "sqlite+aiosqlite:///./data/telegram.db"

    @property
    def clones_path(self) -> Path:
        """Directory for dedicated git clones managed by the indexer."""
        if self.clones_dir:
            path = Path(self.clones_dir)
        else:
            path = Path(__file__).resolve().parent.parent / "var" / "clones"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def project_config_filepath(self) -> Path:
        """Resolve the project config YAML file path."""
        return Path(self.project_config_dir) / "config.yaml"


settings = Settings()
