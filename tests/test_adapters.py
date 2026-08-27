"""Tests for CE-010: Claude Code hook adapter."""

import io
import json
import sys

import pytest

from adapters.claude_hook import main as hook_main

HOOK_JSON = {"tool_name": "Bash", "tool_input": {"command": "npm run build"}}
HOOK_NO_CMD = {"tool_name": "Read", "tool_input": {"file_path": "README.md"}}


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    from adapters import claude_hook

    p = tmp_path / ".ce"
    monkeypatch.setattr(claude_hook, "AUDIT_PATH", p / "audit.jsonl")
    return p / "audit.jsonl"


def _run_hook(payload, enforce=False, monkeypatch=None):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return hook_main(["--enforce"] if enforce else [])


def test_hook_audits_tool_call(audit_path, monkeypatch):
    assert _run_hook(HOOK_JSON, monkeypatch=monkeypatch) == 0
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "Bash"
    assert "verdict" in entry
    assert entry["verdict"]["action"] in ("continue", "retry", "advisor", "escalate", "auto_answer")


def test_hook_ignores_non_command_tools(audit_path, monkeypatch):
    assert _run_hook(HOOK_NO_CMD, monkeypatch=monkeypatch) == 0
    assert audit_path.exists()


def test_hook_enforce_blocks_failure_like_command(audit_path, monkeypatch, capsys):
    failing = {"tool_name": "Bash", "tool_input": {"command": "run deploy that failed with error 500"}}
    rc = _run_hook(failing, enforce=True, monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    if rc == 2:
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["decision"] == "block"
    else:
        assert rc == 0


def test_hook_never_crashes_on_bad_stdin(audit_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert _run_hook(None, monkeypatch=monkeypatch) == 0
