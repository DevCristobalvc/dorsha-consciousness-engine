"""Central configuration for the engine (pydantic-settings).

Load order:
1. defaults below
2. ``CE_*`` environment variables (e.g. ``CE_SESSION_DB``)
3. ``.env`` file in the working directory
4. explicit YAML via ``Settings.from_yaml(path)`` (e.g. ``config/local.yaml``)

No secrets belong here — keys come from the environment.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RecallConfig(BaseModel):
    """Retrieval-augmented recall over session history."""

    top_k: int = Field(default=5, ge=1, le=50)
    max_chars: int = Field(default=4096, ge=256)
    recency_half_life_days: int = Field(default=30, ge=1)


class JudgeConfig(BaseModel):
    """Error / uncertainty detection thresholds."""

    max_attempts: int = Field(default=3, ge=1)
    confidence_floor: float = Field(default=0.6, ge=0.0, le=1.0)
    obvious_ask_policy: str = Field(default="auto_answer", pattern=r"^(auto_answer|ask|log)$")


class LoopConfig(BaseModel):
    """Loop driver (cron worker) settings."""

    tick_interval_min: int = Field(default=5, ge=1)
    idle_timeout_min: int = Field(default=3, ge=1)
    wake_webhook: str = Field(default="", description="gateway webhook URL (set locally, never committed)")
    wake_webhook_secret: str = Field(default="", description="HMAC secret for the webhook (local only)")


class Settings(BaseSettings):
    """Top-level engine settings."""

    model_config = SettingsConfigDict(
        env_prefix="CE_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    session_db: str = Field(default="", description="path to the agent's session SQLite DB (outside this repo)")
    vector_store: str = Field(default="./vectors", description="sqlite-vec index location")
    worker_model: str = Field(default="deepseek-v4-flash")
    advisor_model: str = Field(default="gpt-4o")
    embedding_model: str = Field(default="all-MiniLM-L6-v2")

    recall: RecallConfig = Field(default_factory=RecallConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Settings":
        """Load settings from a YAML file (unknown keys ignored)."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(**data)
