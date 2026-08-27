"""Tests for CE-013: local configuration panel."""

import json
import threading
import urllib.request

import pytest

from engine.config import Settings
from engine.core import Engine
from engine.panel import _apply_config_updates, _mask_key, serve


@pytest.fixture
def server(tmp_path, monkeypatch):
    import engine.panel as panel

    monkeypatch.setattr(panel, "CONFIG_PATH", tmp_path / "config" / "local.yaml")
    settings = Settings(session_db="", vector_store=str(tmp_path / "vectors"))
    eng = Engine(settings, todo_path=str(tmp_path / "TODO.md"))
    srv = serve(port=0, settings=settings, engine=eng, todo_path=str(tmp_path / "TODO.md"))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_panel_serves_html(server):
    with urllib.request.urlopen(server + "/", timeout=5) as r:
        assert r.status == 200
        html = r.read().decode()
    assert "Dorsha Consciousness Engine" in html
    assert "max-iterations" in html


def test_panel_status_endpoint(server):
    status, body = _get(server + "/api/status")
    assert status == 200
    assert "chunks_indexed" in body
    assert "supervise_active" in body
    assert "todo_next" in body


def test_panel_config_masks_key(server):
    # save via the endpoint so the in-memory settings see it
    _post(server + "/api/key", {"api_key": "sk-test-secret-123456789"})
    status, body = _get(server + "/api/config")
    assert status == 200
    assert body["has_key"] is True
    assert "sk-test-secret-123456789" not in json.dumps(body)  # never leaked
    assert body["api_key_masked"].endswith("6789")


def test_panel_save_key(server, tmp_path):
    status, body = _post(server + "/api/key", {"api_key": "sk-panel-key-987654321"})
    assert status == 200
    assert body["masked"].endswith("4321")
    saved = (tmp_path / "config" / "local.yaml").read_text(encoding="utf-8")
    assert "sk-panel-key-987654321" in saved  # persisted (gitignored)
    assert "sk-panel-key-987654321" not in json.dumps(_get(server + "/api/config")[1])


def test_panel_save_config(server, tmp_path):
    status, body = _post(server + "/api/config", {
        "advisor_model": "deepseek-chat", "max_iterations": 5, "max_tokens_per_task": 9000,
    })
    assert status == 200
    saved = (tmp_path / "config" / "local.yaml").read_text(encoding="utf-8")
    assert "deepseek-chat" in saved
    assert "max_iterations: 5" in saved
    assert "9000" in saved


def test_panel_supervise_control(server):
    status, body = _post(server + "/api/supervise/on", {"task": "T1", "max_iterations": 2})
    assert status == 200
    assert body["state"]["active"] is True
    status, body = _post(server + "/api/supervise/off", {})
    assert status == 200
    status, body = _post(server + "/api/supervise/tick", {})
    assert status == 200
    assert body["action"] == "idle_wait"  # loop off → idle


def test_panel_index_endpoint(server):
    status, body = _post(server + "/api/index", {"limit": 0})
    assert status == 200
    assert body["ok"] is True


def test_mask_key():
    assert _mask_key("") == ""
    assert _mask_key("sk-abc") == "••••••"  # 6 chars → 6 dots
    assert _mask_key("sk-1234567890abcd").endswith("abcd")


def test_apply_config_updates_writes_nested(tmp_path):
    import engine.panel as panel

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(panel, "CONFIG_PATH", tmp_path / "config" / "local.yaml")
    local = _apply_config_updates(
        {"advisor_model": "deepseek-chat", "max_iterations": 7, "top_k": 3}, Settings()
    )
    assert local["advisor_model"] == "deepseek-chat"
    assert local["loop"]["max_iterations"] == 7
    assert local["recall"]["top_k"] == 3
