from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./data/project-lens.db"
    debug: bool = False
    project_config_dir: str = ""
    project_config_file: str = "seedsigner.yaml"
    clones_dir: str = ""
    github_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0
    telegram_database_url: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def telegram_db_url(self) -> str:
        """Resolve the Telegram database URL.

        If TELEGRAM_DATABASE_URL is set explicitly, use it. Otherwise derive
        from the main DATABASE_URL by placing a sibling 'telegram.db' file
        next to the main database.
        """
        if self.telegram_database_url:
            return self.telegram_database_url
        # Derive from main DB: replace filename with telegram.db
        main = self.database_url
        prefix = "sqlite+aiosqlite:///"
        if main.startswith(prefix):
            db_path = Path(main[len(prefix):])
            return prefix + str(db_path.parent / "telegram.db")
        return "sqlite+aiosqlite:///./data/telegram.db"

    @property
    def data_path(self) -> Path:
        path = Path(__file__).resolve().parent.parent / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

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
        if self.project_config_dir:
            p = Path(self.project_config_dir)
        else:
            p = Path(__file__).resolve().parent.parent / "config"
        return p / self.project_config_file


settings = Settings()
