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
    """Recall (RAG) settings — selective memory by design."""

    top_k: int = Field(default=5, ge=1)
    max_chars: int = Field(default=4096)
    recency_half_life_days: int = Field(default=30, ge=1)
    index_tools: bool = Field(
        default=False,
        description="index tool outputs too? Default: RAG covers only user prompts, agent replies and saved memory",
    )
    memory_boost: float = Field(default=1.5, ge=1.0, description="score multiplier for explicitly saved memory")


class JudgeConfig(BaseModel):
    """Judge: error/uncertainty detection and routing."""

    max_attempts: int = Field(default=3, ge=1)
    confidence_floor: float = Field(default=0.6, ge=0.0, le=1.0)
    obvious_ask_policy: str = Field(default="auto_answer", description="auto_answer | escalate")
    llm_enabled: bool = Field(default=True, description="LLM-as-judge second pass on ambiguous turns")
    judge_model: str = Field(default="", description="model for the LLM judge (empty = advisor_model)")


class LoopConfig(BaseModel):
    """Loop driver (cron worker) settings."""

    tick_interval_min: int = Field(default=5, ge=1)
    idle_timeout_min: int = Field(default=3, ge=1)
    wake_webhook: str = Field(default="", description="gateway webhook URL (set locally, never committed)")
    wake_webhook_secret: str = Field(default="", description="HMAC secret for the webhook (local only)")
    max_iterations: int = Field(
        default=3, ge=1,
        description="how many auto-iterations the consciousness injects before returning control to the user",
    )
    max_tokens_per_task: int = Field(
        default=0, ge=0,
        description="token budget per task; 0 = unlimited. When exceeded the task is marked pending and the loop stops",
    )


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
    worker_model: str = Field(default="deepseek-v4-flash", description="the agent being supervised (Hermes session model)")
    advisor_model: str = Field(default="deepseek-chat", description="advisor/judge model — official DeepSeek API models only")
    embedding_model: str = Field(default="all-MiniLM-L6-v2")
    api_base: str = Field(default="https://api.deepseek.com/v1", description="OpenAI-compatible base URL for advisor/judge")
    api_key_env: str = Field(default="DEEPSEEK_API_KEY", description="env var holding the API key (fallback OPENAI_API_KEY)")
    api_key: str = Field(default="", description="API key override stored in local config (never committed); wins over api_key_env")

    recall: RecallConfig = Field(default_factory=RecallConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Settings":
        """Load settings from a YAML file (unknown keys ignored)."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(**data)
