"""Application settings.

Sources, in precedence order (highest first):
1. Process environment variables
2. .env file (if present)
3. YAML config file (path from CONFIG_FILE env var, else ./config.yaml)
4. Defaults declared on the Settings class
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class YamlConfigSource(PydanticBaseSettingsSource):
    """Load settings from a YAML file. Missing file = no contribution."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        if yaml_path.is_file():
            with yaml_path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                self._data = {str(k).lower(): v for k, v in loaded.items()}

    def get_field_value(self, field, field_name: str):  # type: ignore[override]
        value = self._data.get(field_name.lower())
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Agent Workflow Metadata API"
    app_version: str = "0.1.0"
    env: str = "dev"
    log_level: str = "INFO"

    host: str = "127.0.0.1"
    port: int = 8000

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "agent_workflows"
    mongodb_connect_timeout_ms: int = 5000
    mongodb_server_selection_timeout_ms: int = 5000
    mongodb_skip_startup: bool = False

    enable_hard_delete: bool = True
    """Whether ``DELETE /workflows/{id}/hard-delete`` is enabled. Production
    deployments should set this to ``False`` and use soft-delete only."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        yaml_path = Path(os.environ.get("CONFIG_FILE", "config.yaml"))
        yaml_source = YamlConfigSource(settings_cls, yaml_path)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
