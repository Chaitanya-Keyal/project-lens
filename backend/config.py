from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./data/project-lens.db"
    debug: bool = False
    project_config_dir: str = ""
    project_config_file: str = "seedsigner.yaml"
    clones_dir: str = ""
    github_token: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

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
