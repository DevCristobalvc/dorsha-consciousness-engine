"""Advisor: reasoning-only second opinion for blocked workers.

The advisor has NO tool access by design — it reads a structured brief and
returns alternatives + a recommendation. Used by the JudgeRouter when retries
are exhausted. Model and key come from config/environment (never hardcoded).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from engine.config import Settings


@dataclass
class AdvisorBrief:
    """What the worker sends when blocked."""

    problem: str
    attempts: list[str] = field(default_factory=list)
    hypothesis: str = ""
    evidence: str = ""


@dataclass
class AdvisorAdvice:
    alternatives: list[str]
    recommendation: str
    confidence: float


class AdvisorError(RuntimeError):
    """Advisor call failed — caller should degrade to escalation."""


class Advisor:
    """Consult a reasoning-only model (OpenAI-compatible)."""

    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self._client = client  # injectable for tests

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise AdvisorError("openai package not installed") from exc
        key = os.environ.get(self.settings.api_key_env) or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise AdvisorError(f"{self.settings.api_key_env} not set")
        return OpenAI(api_key=key, base_url=self.settings.api_base)

    def _build_prompt(self, brief: AdvisorBrief) -> str:
        attempts = "\n".join(f"- {a}" for a in brief.attempts) or "- (none recorded)"
        return (
            "You are an advisor agent for an autonomous worker. Reasoning only, no tools.\n\n"
            f"PROBLEM:\n{brief.problem}\n\n"
            f"ATTEMPTS MADE:\n{attempts}\n\n"
            f"WORKER HYPOTHESIS:\n{brief.hypothesis or '(none)'}\n\n"
            f"EVIDENCE:\n{brief.evidence or '(none)'}\n\n"
            'Respond with JSON only: {"alternatives": ["..."], "recommendation": "...", "confidence": 0.0-1.0}'
        )

    def consult(self, brief: AdvisorBrief) -> AdvisorAdvice:
        """Ask the advisor and parse a structured answer."""
        client = self._get_client()
        try:
            resp = client.chat.completions.create(
                model=self.settings.advisor_model,
                messages=[
                    {"role": "system", "content": "You are a careful, senior engineering advisor."},
                    {"role": "user", "content": self._build_prompt(brief)},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            raise AdvisorError(f"advisor call failed: {exc}") from exc

        content = (resp.choices[0].message.content or "").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AdvisorError("advisor returned invalid JSON") from exc

        alternatives = data.get("alternatives", [])
        if not isinstance(alternatives, list) or not alternatives:
            raise AdvisorError("advisor returned no alternatives")
        return AdvisorAdvice(
            alternatives=[str(a) for a in alternatives],
            recommendation=str(data.get("recommendation", alternatives[0])),
            confidence=float(data.get("confidence", 0.5)),
        )
