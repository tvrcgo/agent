from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ModelConfig(BaseModel):
    provider: str = "openai"
    name: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""


class AgentConfig(BaseModel):
    window_size: int = 50
    max_iterations: int = 100
    system_prompt_path: str = "AGENTS.md"


class WSConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765


class SkillsConfig(BaseModel):
    modules: list[str] = []  # e.g. ["agent.skills.echo", "my_skills.weather"]


class PluginsConfig(BaseModel):
    modules: list[str] = []  # e.g. ["agent.plugins.session"]


class Config(BaseModel):
    model: ModelConfig = ModelConfig()
    agent: AgentConfig = AgentConfig()
    ws: WSConfig = WSConfig()
    skills: SkillsConfig = SkillsConfig()
    plugins: PluginsConfig = PluginsConfig()


def _load_dotenv(path: str | Path = ".env") -> None:
    """Load environment variables from a .env file if it exists."""
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
    """Recursively expand ${VAR} references in string values."""
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
