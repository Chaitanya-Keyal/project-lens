"""Loads project-specific configuration from an external YAML file.

The config defines which repos to track and project metadata.
The YAML path is resolved from Settings.project_config_filepath.
"""
from dataclasses import dataclass, field

import yaml

from project_lens.settings import settings


@dataclass
class RepoConfig:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"


@dataclass
class MergeAuthority:
    login: str
    scope: str  # "technical", "non-technical", "all"
    description: str = ""


@dataclass
class ProjectConfig:
    name: str = "unknown"
    display_name: str = "Unknown Project"
    repos: list[RepoConfig] = field(default_factory=list)
    merge_authorities: list[MergeAuthority] = field(default_factory=list)


def load_project_config() -> ProjectConfig:
    config_file = settings.project_config_filepath

    if not config_file.exists():
        raise FileNotFoundError(
            f"Project config not found: {config_file}\n"
            f"Set PROJECT_CONFIG_DIR in .env or create config/{settings.project_config_file}"
        )

    with open(config_file) as f:
        data = yaml.safe_load(f)

    project = data.get("project", {})
    repos = [RepoConfig(**r) for r in data.get("repos", [])]
    merge_authorities = [
        MergeAuthority(**ma) for ma in data.get("merge_authorities", [])
    ]

    return ProjectConfig(
        name=project.get("name", "unknown"),
        display_name=project.get("display_name", "Unknown Project"),
        repos=repos,
        merge_authorities=merge_authorities,
    )
