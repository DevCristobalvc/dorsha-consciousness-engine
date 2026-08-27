"""LLM-as-judge: second-pass classification when heuristics are inconclusive.

The heuristic detector (``detector.py``) is cheap and instant, but it only
sees markers. When a turn is ambiguous — mixed signals, non-zero exit codes
with no error text, long rambling replies — the LLM judge reads the actual
turn and returns a structured verdict.

Dual-judge cycle (the core thesis of the project):

    LLM as a judge   — classifies every ambiguous turn in hot path
    Human as a judge — resolves only real limitations (blocked/escalate)
                       at the end of the cycle

The LLM judge degrades gracefully: on any failure (no key, timeout, bad JSON)
it returns ``None`` and the caller falls back to the heuristic verdict.
"""

from __future__ import annotations

import json
import logging
import os
import re

from engine.config import Settings
from engine.judge.detector import Verdict, VerdictType

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the consciousness judge of an AI worker agent.
Classify the worker's latest turn into exactly one category:
- ok: normal progress, no problem
- failed: the turn reports an error, crash, failed tool or broken result
- uncertain: the worker hesitates, asks what to do, or shows confusion
- obvious_ask: a yes/no permission question whose answer is obvious from
  context (the worker should decide and execute, not ask)

Return ONLY JSON: {"type": "...", "evidence": "short reason", "suggestion": "action for the worker"}

The worker may be running autonomously. Never let it stall: if the answer to
its question is obvious, say obvious_ask."""


class LLMJudge:
    """OpenAI-compatible judge; client injectable for tests."""

    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client

    def _get_client(self):
        if self.client is not None:
            return self.client
        try:
            from openai import OpenAI

            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                return None
            return OpenAI(api_key=key)
        except Exception:  # noqa: BLE001
            return None

    def _model(self) -> str:
        return self.settings.judge.judge_model or self.settings.advisor_model

    def classify(
        self,
        text: str,
        task_id: str = "default",
        attempts: int = 0,
        tool_exit_codes: list[int] | None = None,
    ) -> Verdict | None:
        """Return a Verdict, or None when the LLM is unavailable or fails."""
        client = self._get_client()
        if client is None:
            return None

        exit_codes = ", ".join(str(c) for c in tool_exit_codes) if tool_exit_codes else "n/a"
        user_prompt = (
            f"Task: {task_id}\n"
            f"Consecutive attempts: {attempts}\n"
            f"Tool exit codes: {exit_codes}\n"
            f"Latest turn:\n---\n{text[:3000]}\n---"
        )
        try:
            resp = client.chat.completions.create(
                model=self._model(),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=200,
            )
            raw = resp.choices[0].message.content
            data = json.loads(raw)
            vtype = VerdictType(data.get("type", "ok"))
            return Verdict(
                type=vtype,
                evidence=data.get("evidence", "llm judge"),
                attempts=attempts,
                suggestion=data.get("suggestion", ""),
                source="llm",
            )
        except Exception as exc:  # noqa: BLE001 — degrade, never crash
            log.warning("llm judge failed (fallback to heuristics): %s", exc)
            return None
