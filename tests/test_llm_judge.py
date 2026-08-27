"""Tests for CE-011: LLM-as-judge second pass."""

import json

import pytest

from engine.config import Settings
from engine.judge.detector import JudgeDetector, VerdictType
from engine.judge.llm_judge import LLMJudge


class FakeOpenAIClient:
    """OpenAI-SDK-shaped fake: client.chat.completions.create(...)"""

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


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, payload):
        self.choices = [_FakeChoice(json.dumps(payload) if not isinstance(payload, str) else payload)]


@pytest.fixture
def settings():
    return Settings()


def test_llm_judge_classifies_ambiguous_turn(settings):
    client = FakeOpenAIClient({"type": "uncertain", "evidence": "worker hesitates", "suggestion": "recall context"})
    j = LLMJudge(settings, client=client)
    v = j.classify("Hmm, no sé cuál camino tomar con este refactor", task_id="t1")
    assert v is not None
    assert v.type is VerdictType.UNCERTAIN
    assert v.source == "llm"
    assert v.suggestion == "recall context"


def test_llm_judge_degrades_on_bad_json(settings):
    client = FakeOpenAIClient("not json at all")
    j = LLMJudge(settings, client=client)
    assert j.classify("anything") is None


def test_llm_judge_degrades_without_client_and_key(settings, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    j = LLMJudge(settings, client=None)
    assert j.classify("anything") is None


def test_detector_uses_llm_on_long_ambiguous_turn(settings):
    client = FakeOpenAIClient({"type": "uncertain", "evidence": "long rambling", "suggestion": "ask advisor"})
    llm = LLMJudge(settings, client=client)
    d = JudgeDetector(settings, llm_judge=llm)
    long_text = "El worker continuó con la tarea y el resultado fue aceptable en general, aunque el enfoque podía mejorarse " * 60
    v = d.classify(long_text, task_id="t2")
    assert v.source == "llm"
    assert v.type is VerdictType.UNCERTAIN


def test_detector_skips_llm_for_short_ok_turn(settings):
    calls = []

    class CountingJudge:
        def classify(self, *a, **kw):
            calls.append(1)
            return None

    d = JudgeDetector(settings, llm_judge=CountingJudge())
    v = d.classify("tarea completada correctamente", task_id="t3")
    assert v.type is VerdictType.OK
    assert calls == []  # no LLM call for clear short turns


def test_detector_llm_disabled(settings):
    settings.judge.llm_enabled = False
    d = JudgeDetector(settings)
    assert d.llm_judge is None


def test_detector_open_question_triggers_llm(settings):
    client = FakeOpenAIClient({"type": "obvious_ask", "evidence": "obvious", "suggestion": "auto_answer"})
    d = JudgeDetector(settings, llm_judge=LLMJudge(settings, client=client))
    v = d.classify("¿Debería hacer el deploy ahora o esperar? " * 8, task_id="t4")
    assert v.source == "llm"
    assert v.type is VerdictType.OBVIOUS_ASK
