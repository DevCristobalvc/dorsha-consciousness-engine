"""Smoke tests for CE-001: imports and config schema."""

import pytest


def test_package_imports():
    import engine  # noqa: F401
    import engine.advisor.advisor  # noqa: F401
    import engine.config  # noqa: F401
    import engine.core  # noqa: F401
    import engine.judge.detector  # noqa: F401
    import engine.judge.router  # noqa: F401
    import engine.loop.idle  # noqa: F401
    import engine.loop.todowatcher  # noqa: F401
    import engine.loop.worker  # noqa: F401
    import engine.recall.indexer  # noqa: F401
    import engine.recall.injector  # noqa: F401
    import engine.recall.retriever  # noqa: F401

    assert engine.__version__ == "0.1.0"


def test_config_defaults():
    from engine.config import Settings

    s = Settings()
    assert s.recall.top_k == 5
    assert s.judge.max_attempts == 3
    assert s.judge.obvious_ask_policy == "auto_answer"
    assert s.loop.idle_timeout_min == 3


def test_config_from_example_yaml():
    from engine.config import Settings

    s = Settings.from_yaml("config/config.example.yaml")
    assert s.recall.top_k == 5
    assert s.judge.max_attempts == 3
    assert s.loop.tick_interval_min == 5
    assert s.worker_model == "deepseek-v4-flash"
    assert s.advisor_model == "gpt-4o"


def test_config_env_override(monkeypatch):
    from engine.config import Settings

    monkeypatch.setenv("CE_SESSION_DB", "/tmp/fake-sessions.db")
    monkeypatch.setenv("CE_RECALL__TOP_K", "9")
    s = Settings()
    assert s.session_db == "/tmp/fake-sessions.db"
    assert s.recall.top_k == 9


def test_config_invalid_threshold_rejected():
    from pydantic import ValidationError

    from engine.config import JudgeConfig

    with pytest.raises(ValidationError):
        JudgeConfig(max_attempts=0)
    with pytest.raises(ValidationError):
        JudgeConfig(confidence_floor=1.5)
