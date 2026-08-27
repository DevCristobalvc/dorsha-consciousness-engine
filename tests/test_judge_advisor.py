"""Tests for CE-004 (judge detector + router) and CE-005 (advisor)."""

import pytest

from engine.advisor.advisor import Advisor, AdvisorAdvice, AdvisorBrief, AdvisorError
from engine.config import Settings
from engine.judge.detector import JudgeDetector, VerdictType
from engine.judge.router import JudgeRouter


@pytest.fixture
def settings():
    return Settings()


def test_detector_ok(settings):
    v = JudgeDetector(settings).classify("Task completed, tests pass")
    assert v.type is VerdictType.OK


def test_detector_failed_marker(settings):
    v = JudgeDetector(settings).classify("The deploy failed with error 500")
    assert v.type is VerdictType.FAILED
    assert v.attempts == 1


def test_detector_failed_exit_code(settings):
    v = JudgeDetector(settings).classify("done", tool_exit_codes=[0, 1])
    assert v.type is VerdictType.FAILED


def test_detector_counts_consecutive_attempts(settings):
    d = JudgeDetector(settings)
    d.classify("error one")
    v = d.classify("error two")
    assert v.type is VerdictType.FAILED
    assert v.attempts == 2


def test_detector_uncertain(settings):
    v = JudgeDetector(settings).classify("No sé qué hacer, no tengo claro cómo seguir")
    assert v.type is VerdictType.UNCERTAIN


def test_detector_obvious_ask(settings):
    v = JudgeDetector(settings).classify("¿Te lo hago?")
    assert v.type is VerdictType.OBVIOUS_ASK
    assert v.suggestion == "auto_answer"


def test_detector_resets_after_ok(settings):
    d = JudgeDetector(settings)
    d.classify("error one")
    d.classify("all good now")
    v = d.classify("error again")
    assert v.attempts == 1  # counter reset by the ok turn


def test_router_retry_then_advisor_then_escalate(settings):
    r = JudgeRouter(settings)
    d = JudgeDetector(settings)

    v1 = d.classify("error one")
    assert r.decide(v1).action == "retry"
    v2 = d.classify("error two")
    assert r.decide(v2).action == "retry"
    v3 = d.classify("error three")  # max_attempts = 3
    assert r.decide(v3).action == "advisor"
    v4 = d.classify("error four")
    assert r.decide(v4).action == "escalate"


def test_router_obvious_ask_auto_answers(settings):
    r = JudgeRouter(settings)
    d = JudgeDetector(settings)
    v = d.classify("¿Arranco?")
    assert r.decide(v).action == "auto_answer"


def test_router_uncertain_advisor_then_escalate(settings):
    r = JudgeRouter(settings)
    d = JudgeDetector(settings)
    v1 = d.classify("No sé cómo seguir")
    assert r.decide(v1).action == "advisor"
    v2 = d.classify("No sé cómo seguir")
    assert r.decide(v2).action == "escalate"


class FakeOpenAIClient:
    """Minimal OpenAI-SDK-shaped fake: client.chat.completions.create(...)."""

    def __init__(self, payload):
        self.payload = payload
        self._chat = _FakeChat(payload)

    @property
    def chat(self):
        return self._chat


class _FakeChat:
    def __init__(self, payload):
        self.payload = payload

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        return _FakeResp(self.payload)


class _FakeResp:
    def __init__(self, payload):
        self.choices = [_FakeChoice(payload)]


class _FakeChoice:
    def __init__(self, payload):
        self.message = _FakeMsg(payload)


class _FakeMsg:
    def __init__(self, payload):
        self.content = payload


def test_advisor_parses_advice(settings):
    payload = '{"alternatives": ["use sqlite-vec", "switch to chroma"], "recommendation": "use sqlite-vec", "confidence": 0.8}'
    advisor = Advisor(settings, client=FakeOpenAIClient(payload))
    advice = advisor.consult(AdvisorBrief(problem="vector store slow", attempts=["tried chroma"], hypothesis="vec0 is fine"))
    assert isinstance(advice, AdvisorAdvice)
    assert len(advice.alternatives) == 2
    assert advice.recommendation == "use sqlite-vec"
    assert advice.confidence == 0.8


def test_advisor_rejects_invalid_json(settings):
    advisor = Advisor(settings, client=FakeOpenAIClient("not json"))
    with pytest.raises(AdvisorError):
        advisor.consult(AdvisorBrief(problem="x"))


def test_advisor_rejects_empty_alternatives(settings):
    advisor = Advisor(settings, client=FakeOpenAIClient('{"alternatives": [], "recommendation": "", "confidence": 0.1}'))
    with pytest.raises(AdvisorError):
        advisor.consult(AdvisorBrief(problem="x"))
