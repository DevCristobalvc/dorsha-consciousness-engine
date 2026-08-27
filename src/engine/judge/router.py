"""Judge router: decides the engine action from a stream of verdicts.

Decision path (CE-004):

    ok          → continue
    failed      → retry (attempts < max) | advisor (attempts == max) | escalate (advisor consulted)
    uncertain   → recall → advisor → escalate
    obvious_ask → auto_answer (no human escalation)
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.config import Settings
from engine.judge.detector import Verdict, VerdictType


@dataclass
class Decision:
    action: str  # continue | retry | advisor | escalate | auto_answer
    reason: str
    attempts: int = 0


class JudgeRouter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._advisor_consulted: dict[str, bool] = {}

    def _consulted(self, task_id: str) -> bool:
        return self._advisor_consulted.get(task_id, False)

    def decide(self, verdict: Verdict, task_id: str = "default") -> Decision:
        max_a = self.settings.judge.max_attempts

        if verdict.type is VerdictType.OK:
            return Decision("continue", "verdict ok", verdict.attempts)

        if verdict.type is VerdictType.OBVIOUS_ASK:
            return Decision("auto_answer", "obvious question with clear answer", 0)

        if verdict.type is VerdictType.FAILED:
            if verdict.attempts < max_a:
                return Decision("retry", f"attempt {verdict.attempts}/{max_a}", verdict.attempts)
            if not self._consulted(task_id):
                self._advisor_consulted[task_id] = True
                return Decision("advisor", "max attempts reached, consulting advisor", verdict.attempts)
            return Decision("escalate", "advisor already consulted, escalating to human", verdict.attempts)

        if verdict.type is VerdictType.UNCERTAIN:
            if not self._consulted(task_id):
                self._advisor_consulted[task_id] = True
                return Decision("advisor", "uncertain choice, consulting advisor", 0)
            return Decision("escalate", "uncertainty unresolved after advisor", 0)

        return Decision("continue", "unclassified", verdict.attempts)
