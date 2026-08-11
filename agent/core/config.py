from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ModelConfig(BaseModel):
    name: str
    max_tokens: int = 128000


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str = ""
    models: dict[str, ModelConfig] = {}


class ModelSection(BaseModel):
    providers: dict[str, ProviderConfig] = {}

    class Alias(BaseModel):
        main: str = "openai:default"
        flash: str | None = None
        embedding: str | None = None

    alias: Alias = Alias()


class AgentConfig(BaseModel):
    max_iterations: int = 100
    max_concurrent: int = 10
    stream: bool = False
    steering: dict[str, Any] = {}


class Config(BaseModel):
    model: ModelSection = ModelSection()
    agent: AgentConfig = AgentConfig()
    tools: list[str | dict[str, Any]] = []
    plugins: list[str | dict[str, Any]] = []


def _load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if (value.startswith("'") and value.endswith("'")) or (
                value.startswith('"') and value.endswith('"')
            ):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_config(path: str | Path = "config.yml") -> Config:
    _load_dotenv()
    p = Path(path)
    if not p.exists():
        config = Config()
    else:
        raw = yaml.safe_load(p.read_text()) or {}
        expanded = _expand_env_vars(raw)
        config = Config(**expanded)

    return config
