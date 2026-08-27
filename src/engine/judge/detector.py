"""Judge detector: classifies a worker turn into ok | failed | uncertain | obvious_ask.

Heuristics first (regex markers), LLM-as-judge only when heuristics are
inconclusive. Thresholds (``max_attempts``, ``confidence_floor``) come from
``Settings.judge``.

The ``obvious_ask`` class encodes the project rule: never ask the human a
yes/no question when the answer is obvious — auto-answer instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from engine.config import Settings


class VerdictType(str, Enum):
    OK = "ok"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    OBVIOUS_ASK = "obvious_ask"


@dataclass
class Verdict:
    """Structured classification of one worker turn."""

    type: VerdictType
    evidence: str
    attempts: int = 0
    suggestion: str = ""
    confidence: float = 1.0


# Markers for repeated failures / hard errors
FAILURE_RE = re.compile(
    r"\b(?:error|failed|failure|fatal|exception|traceback|crash|panic|timeout|timed out|"
    r"exit code [1-9]\d*|returned nonzero|segmentation fault|killed)\b",
    re.IGNORECASE,
)

# Markers for uncertainty / asking for direction without exhausting options
UNCERTAIN_RE = re.compile(
    r"\b(?:no s[eé]|no estoy seguro|no est[aá] seguro|not sure|unsure|uncertain|"
    r"i (?:don'?t|do not) know|i'?m lost|qu[eé] hago|what should i do|"
    r"no tengo claro|no entiendo c[oó]mo seguir|at a loss|duda)\b",
    re.IGNORECASE,
)

# Permission-style questions whose answer is usually obvious from context
OBVIOUS_ASK_RE = re.compile(
    r"\b(?:te lo hago|lo hago|sigo|contin[uú]o|procedo|arranco|empiezo|should i|"
    r"want me to|do you want me to|do i proceed|shall i|quer[eé]s que)\b[^\n?]*\?",
    re.IGNORECASE,
)


class JudgeDetector:
    """Classify worker turns; track consecutive attempts per task."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._attempts: dict[str, int] = {}

    def reset(self, task_id: str) -> None:
        self._attempts.pop(task_id, None)

    def _attempts_for(self, task_id: str) -> int:
        return self._attempts.get(task_id, 0)

    def classify(
        self,
        text: str,
        task_id: str = "default",
        tool_exit_codes: list[int] | None = None,
    ) -> Verdict:
        """Classify a turn and update the consecutive-attempt counter."""
        text = text or ""
        exit_codes = tool_exit_codes or []

        hard_fail = bool(exit_codes and any(c != 0 for c in exit_codes))
        marker_fail = bool(FAILURE_RE.search(text))
        attempts = self._attempts_for(task_id)

        if hard_fail or marker_fail:
            self._attempts[task_id] = attempts + 1
            n = self._attempts[task_id]
            return Verdict(
                type=VerdictType.FAILED,
                evidence=f"error markers / exit codes {exit_codes}" if hard_fail else "failure markers in output",
                attempts=n,
                suggestion=f"retry (attempt {n}/{self.settings.judge.max_attempts})",
                confidence=0.9 if hard_fail else 0.7,
            )

        if OBVIOUS_ASK_RE.search(text):
            # answer is obvious from context — do not escalate to the human
            self._attempts[task_id] = 0
            return Verdict(
                type=VerdictType.OBVIOUS_ASK,
                evidence="permission-style question with obvious answer",
                attempts=0,
                suggestion=self.settings.judge.obvious_ask_policy,
                confidence=0.85,
            )

        if UNCERTAIN_RE.search(text):
            self._attempts[task_id] = 0
            return Verdict(
                type=VerdictType.UNCERTAIN,
                evidence="uncertainty markers in output",
                attempts=0,
                suggestion="recall context → advisor if unresolved",
                confidence=0.6,
            )

        self._attempts[task_id] = 0
        return Verdict(type=VerdictType.OK, evidence="no markers", attempts=0, suggestion="continue")
