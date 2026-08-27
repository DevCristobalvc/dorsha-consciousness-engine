"""Local configuration panel for the Dorsha Consciousness Engine (CE-013).

A zero-dependency web panel (stdlib ``http.server``) bound to loopback only.
Lets the user configure the engine without touching files: API key, models,
loop iterations, token budget, thresholds — plus live status and supervision
controls.

Endpoints:
    GET  /                        panel UI (single-file HTML/JS)
    GET  /api/status              engine + supervised-loop status
    GET  /api/config              current config (API key masked)
    POST /api/config              update config values (no key here)
    POST /api/key                 save the API key (masked in responses)
    POST /api/supervise/on        start supervised loop (body: task, max_iterations, max_tokens)
    POST /api/supervise/off       stop supervised loop
    POST /api/supervise/tick      run one supervised tick
    POST /api/index               run the embedding indexer

Security: binds 127.0.0.1 only; the API key is never returned in plain text;
persisted config lives in config/local.yaml (gitignored).
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from engine.config import Settings
from engine.core import Engine

log = logging.getLogger(__name__)

DEFAULT_PORT = 8899
CONFIG_PATH = Path("config/local.yaml")


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:3] + "•" * 10 + key[-4:]


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**_load_config(), **data}
    CONFIG_PATH.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")


def _flatten_for_panel(settings: Settings) -> dict:
    return {
        "api_base": settings.api_base,
        "api_key_env": settings.api_key_env,
        "api_key_masked": _mask_key(settings.api_key or ""),
        "has_key": bool(settings.api_key),
        "worker_model": settings.worker_model,
        "advisor_model": settings.advisor_model,
        "embedding_model": settings.embedding_model,
        "max_attempts": settings.judge.max_attempts,
        "llm_enabled": settings.judge.llm_enabled,
        "top_k": settings.recall.top_k,
        "max_chars": settings.recall.max_chars,
        "recency_half_life_days": settings.recall.recency_half_life_days,
        "max_iterations": settings.loop.max_iterations,
        "max_tokens_per_task": settings.loop.max_tokens_per_task,
        "idle_timeout_min": settings.loop.idle_timeout_min,
        "session_db": settings.session_db,
        "vector_store": settings.vector_store,
    }


def _apply_config_updates(data: dict, settings: Settings) -> dict:
    """Map panel fields to local.yaml structure. Returns the new local config dict."""
    local = _load_config()
    top = {
        "api_base": data.get("api_base"),
        "worker_model": data.get("worker_model"),
        "advisor_model": data.get("advisor_model"),
        "embedding_model": data.get("embedding_model"),
        "session_db": data.get("session_db"),
        "vector_store": data.get("vector_store"),
    }
    for k, v in top.items():
        if v is not None:
            local[k] = v
    recall = dict(local.get("recall", {}))
    for k in ("top_k", "max_chars", "recency_half_life_days"):
        if data.get(k) is not None:
            recall[k] = int(data[k]) if k != "max_chars" else int(data[k])
    if recall:
        local["recall"] = recall
    judge = dict(local.get("judge", {}))
    if data.get("max_attempts") is not None:
        judge["max_attempts"] = int(data["max_attempts"])
    if data.get("llm_enabled") is not None:
        judge["llm_enabled"] = bool(data["llm_enabled"])
    if judge:
        local["judge"] = judge
    loop = dict(local.get("loop", {}))
    for k in ("max_iterations", "max_tokens_per_task", "idle_timeout_min"):
        if data.get(k) is not None:
            loop[k] = int(data[k])
    if loop:
        local["loop"] = loop
    return local


PANEL_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ce — panel</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#c9d1d9;
          --dim:#8b949e; --accent:#3fb950; --cyan:#58a6ff; --red:#f85149; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font:14px/1.5 "JetBrains Mono", ui-monospace, monospace; padding:24px; max-width:880px; margin:0 auto; }
  h1 { font-size:16px; color:var(--cyan); margin-bottom:4px; }
  h1 span { color:var(--dim); font-weight:400; }
  .sub { color:var(--dim); font-size:12px; margin-bottom:20px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:16px; }
  .card h2 { font-size:13px; color:var(--accent); margin-bottom:12px; text-transform:uppercase; letter-spacing:.05em; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .field { display:flex; flex-direction:column; gap:4px; }
  .field label { font-size:11px; color:var(--dim); }
  .field input, .field select { background:var(--bg); border:1px solid var(--border); color:var(--text);
    border-radius:6px; padding:7px 9px; font:13px ui-monospace,monospace; }
  .field input:focus, .field select:focus { outline:none; border-color:var(--cyan); }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  button { background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:6px;
    padding:7px 14px; font:13px ui-monospace,monospace; cursor:pointer; }
  button:hover { border-color:var(--accent); color:var(--accent); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#0d1117; font-weight:700; }
  button.danger:hover { border-color:var(--red); color:var(--red); }
  .chips { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; }
  .chip { background:var(--bg); border:1px solid var(--border); border-radius:20px; padding:4px 12px; font-size:12px; }
  .chip b { color:var(--cyan); }
  .chip.ok b { color:var(--accent); }
  .msg { margin-top:10px; font-size:12px; min-height:18px; }
  .msg.ok { color:var(--accent); }
  .msg.err { color:var(--red); }
  .mono { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:8px 10px; font-size:12px; white-space:pre-wrap; word-break:break-word; }
  footer { color:var(--dim); font-size:11px; text-align:center; margin-top:24px; }
</style>
</head>
<body>
  <h1>Dorsha Consciousness Engine <span>— panel de configuracion</span></h1>
  <div class="sub">$ ce panel · loopback · los cambios se guardan en config/local.yaml (gitignored)</div>

  <div class="card">
    <h2>Estado</h2>
    <div class="chips" id="chips"><div class="chip">cargando…</div></div>
    <div class="mono" id="status-box">—</div>
  </div>

  <div class="card">
    <h2>Supervisar (loop)</h2>
    <div class="row">
      <button onclick="supervise('on')">ON</button>
      <button onclick="supervise('off')">OFF</button>
      <button onclick="supervise('tick')">tick</button>
      <span class="sub" id="super-state"></span>
    </div>
    <div class="grid" style="margin-top:10px">
      <div class="field"><label>Task</label><input id="sv-task" value="default"></div>
      <div class="field"><label>Max iteraciones</label><input id="sv-it" type="number" value="3" min="1"></div>
    </div>
  </div>

  <div class="card">
    <h2>API key (DeepSeek por defecto)</h2>
    <div class="grid">
      <div class="field"><label>API key <span id="key-status"></span></label>
        <input id="api-key" type="password" placeholder="sk-…"></div>
      <div class="field"><label>Base URL</label><input id="api-base"></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="primary" onclick="saveKey()">Guardar key</button>
      <button onclick="indexNow()">Indexar historial</button>
    </div>
    <div class="msg" id="key-msg"></div>
  </div>

  <div class="card">
    <h2>Modelos y presupuesto</h2>
    <div class="grid">
      <div class="field"><label>Worker model</label><input id="worker-model"></div>
      <div class="field"><label>Advisor / judge model</label><input id="advisor-model"></div>
      <div class="field"><label>Embedding model</label><input id="embedding-model"></div>
      <div class="field"><label>Max iteraciones por prompt</label><input id="max-iterations" type="number" min="1"></div>
      <div class="field"><label>Max tokens por tarea (0 = ilimitado)</label><input id="max-tokens" type="number" min="0"></div>
      <div class="field"><label>Max attempts (judge)</label><input id="max-attempts" type="number" min="1"></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="primary" onclick="saveConfig()">Guardar config</button>
    </div>
    <div class="msg" id="cfg-msg"></div>
  </div>

  <footer>github.com/DevCristobalvc/dorsha-consciousness-engine</footer>

<script>
async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
function show(id, text, ok) { const el = document.getElementById(id); el.textContent = text; el.className = "msg " + (ok ? "ok" : "err"); }

async function loadStatus() {
  try {
    const st = await api("/api/status");
    const chips = document.getElementById("chips");
    chips.innerHTML = [
      chip("chunks", st.chunks_indexed),
      chip("loop", st.supervise_active ? "ON" : "OFF", st.supervise_active),
      chip("iteraciones", st.supervise_active ? st.supervise_iterations : "—"),
      chip("tarea", st.todo_next || "all done"),
      chip("key", st.has_key ? "set" : "missing", st.has_key),
    ].join("");
    document.getElementById("status-box").textContent = JSON.stringify(st, null, 2);
  } catch (e) { show("key-msg", "status: " + e.message, false); }
}
function chip(label, value, ok) {
  return '<div class="chip' + (ok ? " ok" : "") + '">' + label + ': <b>' + value + "</b></div>";
}

async function loadConfig() {
  try {
    const c = await api("/api/config");
    set("api-base", c.api_base); set("worker-model", c.worker_model);
    set("advisor-model", c.advisor_model); set("embedding-model", c.embedding_model);
    set("max-iterations", c.max_iterations); set("max-tokens", c.max_tokens_per_task);
    set("max-attempts", c.max_attempts);
    document.getElementById("key-status").textContent = c.api_key_masked ? "(guardada " + c.api_key_masked + ")" : "(no configurada — se usa " + c.api_key_env + " del env)";
  } catch (e) { show("cfg-msg", "config: " + e.message, false); }
}
function set(id, v) { document.getElementById(id).value = v ?? ""; }

async function saveKey() {
  const v = document.getElementById("api-key").value.trim();
  if (!v) return show("key-msg", "escribe la API key", false);
  try { const r = await api("/api/key", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ api_key: v }) });
    show("key-msg", "key guardada (" + r.masked + ")", true); document.getElementById("api-key").value = ""; loadStatus();
  } catch (e) { show("key-msg", e.message, false); }
}
async function saveConfig() {
  const body = {
    api_base: val("api-base"), worker_model: val("worker-model"), advisor_model: val("advisor-model"),
    embedding_model: val("embedding-model"), max_iterations: num("max-iterations"),
    max_tokens_per_task: num("max-tokens"), max_attempts: num("max-attempts"),
  };
  try { await api("/api/config", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) });
    show("cfg-msg", "config guardada en config/local.yaml", true);
  } catch (e) { show("cfg-msg", e.message, false); }
}
function val(id) { return document.getElementById(id).value.trim(); }
function num(id) { const v = parseInt(document.getElementById(id).value, 10); return Number.isNaN(v) ? null : v; }

async function supervise(cmd) {
  try {
    if (cmd === "on") {
      await api("/api/supervise/on", { method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ task: val("sv-task"), max_iterations: num("sv-it") }) });
      show("key-msg", "loop supervisado ON", true);
    } else if (cmd === "off") { await api("/api/supervise/off", { method: "POST" }); show("key-msg", "loop supervisado OFF", true); }
    else { const t = await api("/api/supervise/tick", { method: "POST" }); show("key-msg", "tick: " + t.action, true); }
    loadStatus();
  } catch (e) { show("key-msg", e.message, false); }
}
async function indexNow() {
  try { show("key-msg", "indexando…", true);
    const r = await api("/api/index", { method: "POST" });
    show("key-msg", "indexado: " + JSON.stringify(r.stats), true); loadStatus();
  } catch (e) { show("key-msg", "index: " + e.message, false); }
}

loadStatus(); loadConfig();
</script>
</body>
</html>
"""


class PanelHandler(BaseHTTPRequestHandler):
    engine: Engine | None = None  # set by serve()
    settings: Settings | None = None

    def log_message(self, format, *args):  # noqa: A002 — quiet access log
        log.debug(format, *args)

    def _send(self, code: int, body: str | bytes, ctype: str = "application/json") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data), "application/json")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def _status_payload(self) -> dict:
        st = self.engine.status()
        sup = self.engine.loop.watcher
        # supervised loop state
        from engine.loop.supervised import SupervisedLoop

        sl = SupervisedLoop(self.settings, self.engine, todo_path="TODO.md")
        sup_status = sl.status()
        return {
            **st,
            "supervise_active": sup_status["active"],
            "supervise_iterations": sup_status["iterations"],
            "supervise_task": sup_status["task"],
            "has_key": bool(self.settings.api_key),
        }

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, PANEL_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(200, self._status_payload())
        elif self.path == "/api/config":
            self._json(200, _flatten_for_panel(self.settings))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_body()
        if self.path == "/api/key":
            key = (body.get("api_key") or "").strip()
            if not key:
                self._json(400, {"error": "api_key required"})
                return
            _save_config({"api_key": key})
            self.settings.api_key = key
            self._json(200, {"ok": True, "masked": _mask_key(key)})
        elif self.path == "/api/config":
            local = _apply_config_updates(body, self.settings)
            _save_config(local)
            self._json(200, {"ok": True, "saved": list(body.keys())})
        elif self.path == "/api/supervise/on":
            from engine.loop.supervised import SupervisedLoop

            sl = SupervisedLoop(self.settings, self.engine, todo_path="TODO.md")
            state = sl.start(body.get("task", "default"), max_iterations=body.get("max_iterations"),
                             max_tokens=body.get("max_tokens"))
            self._json(200, {"ok": True, "state": state})
        elif self.path == "/api/supervise/off":
            from engine.loop.supervised import SupervisedLoop

            SupervisedLoop(self.settings, self.engine, todo_path="TODO.md").stop(body.get("reason", "panel"))
            self._json(200, {"ok": True})
        elif self.path == "/api/supervise/tick":
            from engine.loop.supervised import SupervisedLoop

            t = SupervisedLoop(self.settings, self.engine, todo_path="TODO.md").tick()
            self._json(200, {"action": t.action, "message": t.message[:200], "iteration": t.iteration})
        elif self.path == "/api/index":
            from engine.recall.indexer import Indexer

            stats = Indexer(self.settings).index(limit=int(body.get("limit") or 0))
            self._json(200, {"ok": True, "stats": stats})
        else:
            self._json(404, {"error": "not found"})


def serve(port: int = DEFAULT_PORT, host: str = "127.0.0.1", settings: Settings | None = None,
          engine: Engine | None = None, todo_path: str | Path | None = None) -> ThreadingHTTPServer:
    """Start the panel server (loopback only). Returns the server (call serve_forever)."""
    if settings is None:
        settings = Settings.from_yaml(str(CONFIG_PATH)) if CONFIG_PATH.exists() else Settings()
    engine = engine or Engine(settings, todo_path=todo_path or Path.cwd() / "TODO.md")
    PanelHandler.engine = engine
    PanelHandler.settings = settings
    server = ThreadingHTTPServer((host, port), PanelHandler)
    log.info("panel on http://%s:%s (loopback only)", host, port)
    return server
