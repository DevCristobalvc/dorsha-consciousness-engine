#!/usr/bin/env python3
"""Claude Code PreToolUse hook — the consciousness observes every tool call.

Audit mode (default): logs the judge verdict for every tool call to
``.ce/audit.jsonl`` (append-only). Enforce mode (``--enforce``) additionally
blocks tool calls the judge flags as failure-prone.

Configure in ``.claude/settings.json``:

    {
      "hooks": {
        "PreToolUse": [
          { "matcher": "Bash|Write|Edit",
            "hooks": [{ "type": "command", "command": "python3 adapters/claude-hook.py" }] }
        ]
      }
    }

Hook input (JSON) arrives on stdin: {"tool_name": "...", "tool_input": {...}}.
Exit 2 + JSON payload blocks the tool call (enforce mode only).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AUDIT_PATH = Path(".ce/audit.jsonl")


def _judge(text: str) -> dict:
    try:
        from engine.config import Settings
        from engine.core import Engine

        engine = Engine(Settings.from_yaml("config/local.yaml"))
        decision = engine.judge(text, task_id="claude-tool")
        return {"action": decision.action, "reason": decision.reason, "attempts": decision.attempts}
    except Exception as exc:  # noqa: BLE001 — never break the agent's workflow
        return {"action": "continue", "reason": f"engine unavailable: {exc}", "attempts": 0}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    enforce = "--enforce" in argv

    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # not a hook event — ignore
    if not isinstance(data, dict):
        return 0  # unexpected payload — ignore

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    text = tool_input.get("command", "") if isinstance(tool_input, dict) else json.dumps(tool_input)
    text = text or tool

    verdict = _judge(text[:2000])

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "verdict": verdict}) + "\n")

    if enforce and verdict["action"] in ("retry", "escalate"):
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "decision": "block",
                "reason": f"consciousness judge: {verdict['reason']}",
            }
        }
        print(json.dumps(payload))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
