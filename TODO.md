# TODO — Agent Consciousness Engine

> Structured task tracking. Format: Title / Description / Use Cases / Acceptance Criteria / Tests / Docs.
> Worker loop pattern: each task moves to `Done` only when acceptance criteria are verified.

---

## TASK: CE-001 — Scaffold repository structure

### Title
Formal repo scaffold with module layout and config skeleton

### Description
Set up the repository structure (src/, config/, docs/), pyproject.toml, .gitignore and base configuration for model routing and storage paths.

### Use Cases
- UC1: Developer clones repo and installs deps (`pip install -e .`)
- UC2: Config file centralizes thresholds, models and paths
- UC3: CI can run tests from a clean checkout

### Acceptance Criteria
- AC1: `pip install -e .` completes without errors
- AC2: `config/` loads via pydantic settings
- AC3: Empty module stubs import cleanly (`python -c "import engine"`)

### Tests
- `pytest` passes a smoke test that imports all modules
- Config schema validated with a sample YAML

### Docs / Comments
- Python 3.11+, pydantic v2, sqlite-vec for vectors
- Session history DB (Hermes) lives outside this repo — path set in config

---

## TASK: CE-002 — Recall: session-history indexer

### Title
Index Hermes session DB into a vector store for RAG recall

### Description
Build `src/recall/indexer.py`: read the session SQLite DB (messages, sessions), chunk messages, embed with a local model, store vectors (sqlite-vec) with provenance (session_id, message_id, timestamp, role).

### Use Cases
- UC1: Full session history indexed incrementally (only new messages)
- UC2: Embeddings stored with provenance for citation
- UC3: Re-index idempotent (no duplicates)

### Acceptance Criteria
- AC1: Indexer processes the live session DB without locking it
- AC2: Vector store grows only with new messages (incremental)
- AC3: Every chunk stores `session_id`, `message_id`, `timestamp`, `role`

### Tests
- Index N known messages → retrieve each one back with its provenance
- Re-run indexer → count unchanged (idempotent)
- 10K+ messages indexed in < 2 min on the dev box

### Docs / Comments
- Embedding model: sentence-transformers `all-MiniLM-L6-v2` (local, no API cost)
- Vector store: `sqlite-vec` (zero-config, lives beside the DB)
- Do NOT index tool dumps verbatim — strip large blobs, keep summaries

---

## TASK: CE-003 — Recall: retriever + injector

### Title
Retrieval endpoint that injects relevant history into worker context

### Description
Build `src/recall/retriever.py` (semantic + recency hybrid search) and `src/recall/injector.py` (formats retrieved chunks as a context block with provenance footer).

### Use Cases
- UC1: Given a query (current task/uncertainty), return top-K relevant messages
- UC2: Injector produces a `[RECALL]` block with citations
- UC3: Recency boosts recent messages without drowning semantic hits

### Acceptance Criteria
- AC1: `retriever.query(text, k=5)` returns chunks + scores + provenance
- AC2: Injection block is < 4KB default (configurable)
- AC3: Citations format: `session:<id> msg:<id> @ <timestamp>`

### Tests
- Query about a past task → correct session retrieved
- Hybrid ranking: recent exact match beats old semantic match (and vice versa)

### Docs / Comments
- Hybrid: cosine similarity * recency decay factor (half-life 30 days)
- Injectable via engine event `context_exhausted` / `uncertain_choice`

---

## TASK: CE-004 — Judge: error & uncertainty detector

### Title
Detect repeated failures and low-confidence decisions from worker output

### Description
Build `src/judge/detector.py`: parse worker turns — count consecutive failures (tool errors, exit codes, retry markers), detect uncertainty signals (hedging, explicit "I don't know", repeated ask-back), and flag broken acceptance criteria.

### Use Cases
- UC1: 3+ consecutive failures → raise `attempt_failed`
- UC2: Worker asks the human a question with an obvious answer → raise `obvious_ask` (auto-answer policy)
- UC3: Low-confidence phrasing → raise `uncertain_choice`

### Acceptance Criteria
- AC1: Detector classifies each turn into `ok | failed | uncertain | obvious_ask`
- AC2: Thresholds come from `config/thresholds.yaml` (attempts, confidence floor)
- AC3: Output is a structured verdict object (type, evidence, suggestion)

### Tests
- Fixture: N consecutive tool errors → verdict `failed` with count
- Fixture: "no sé qué hacer, ¿sigo?" → `uncertain` ; "¿te lo hago?" after explicit yes → `obvious_ask`

### Docs / Comments
- Heuristics first (regex/markers), LLM-as-judge only when heuristics are inconclusive
- The `obvious_ask` rule encodes the project rule: never ask yes/no when the answer is obvious

---

## TASK: CE-005 — Advisor: second-opinion agent

### Title
Reasoning-only advisor (GPT-4o class) for blocked states

### Description
Build `src/advisor/advisor.py`: given problem + attempts + hypothesis, consult a reasoning-only model (no tools) and return alternatives, risks and a recommended path. Used by the Judge when retries are exhausted.

### Use Cases
- UC1: Worker blocked → advisor receives structured brief
- UC2: Advisor returns 2–3 alternative approaches with trade-offs
- UC3: Advisor response logged for later review

### Acceptance Criteria
- AC1: Advisor call uses the configured reasoning model (separate from worker model)
- AC2: Brief format: `problem / attempts / hypothesis / evidence`
- AC3: Response includes explicit recommendation + confidence

### Tests
- Mock blocked scenario → advisor returns ≥ 2 alternatives
- Timeout/error handling: advisor failure degrades to direct escalation

### Docs / Comments
- Model routed via config (`advisor.model`); uses the human's own API key, never hardcoded
- Advisor has NO tool access by design — reasoning only

---

## TASK: CE-006 — Loop: TODO watcher + idle detection

### Title
Cron worker that reads TODO.md, generates next prompt and wakes idle agents

### Description
Build `src/loop/worker.py` + `src/loop/todowatcher.py` + `src/loop/idle.py`: on each tick read the active TODO.md, find `IN_PROGRESS`/`BLOCKED` tasks, generate the next action for the worker, and if the worker has been idle > T minutes, inject a wake message via webhook.

### Use Cases
- UC1: Tick reads TODO and produces a next-step prompt
- UC2: Idle worker (>3 min) gets a wake injection + webhook call
- UC3: BLOCKED tasks are skipped until unblocked by human/advisor

### Acceptance Criteria
- AC1: Worker runs on schedule (cron / systemd timer), interval configurable
- AC2: Wake message contains the last user exchange + TODO state
- AC3: No wake spam: wake fires at most once per idle window

### Tests
- Simulated idle state → exactly one wake event per window
- TODO with 3 tasks → worker proposes the correct next task (acceptance criteria check)

### Docs / Comments
- Pattern: `dorsha-cron-loop-worker` skill (proven in production since jul 2026)
- Webhook endpoint configurable in `config/`

---

## TASK: CE-007 — Engine orchestration + CLI

### Title
`engine.py` entrypoint wiring recall → judge → loop with a CLI

### Description
Single entrypoint that subscribes to worker events and routes them: `context_exhausted` → recall; `attempt_failed`/`uncertain_choice` → judge (retry → advisor → escalate); `idle_timeout` → loop wake. Provide a CLI (`ce status`, `ce recall <query>`, `ce judge <turn>`, `ce loop on|off|status`).

### Use Cases
- UC1: `ce status` shows engine state (last event, recalls, verdicts)
- UC2: `ce recall "what did we decide about X"` returns cited history
- UC3: `ce loop on` starts the worker in foreground

### Acceptance Criteria
- AC1: All three subsystems callable from the CLI
- AC2: Events logged with timestamps and routing decisions
- AC3: Exit codes: 0 ok, 1 blocked/escalated

### Tests
- End-to-end fixture: worker fails 3× → judge → advisor consulted → escalation logged
- CLI commands run under 1s startup

### Docs / Comments
- Logging: structured (JSON lines), no secrets
- Event bus: simple in-process queue first; swap to pub/sub if multi-agent

---

## TASK: CE-008 — Documentation & examples

### Title
Architecture doc, protocol spec and a worked example

### Description
Write `docs/architecture.md` (diagrams, data flow), `docs/protocol.md` (events table, payload schemas) and an example walkthrough (worker blocked on a real task → engine recalls, judges, advises, escalates).

### Use Cases
- UC1: New contributor understands the engine in < 30 min
- UC2: Protocol is implementable by any agent framework (not Hermes-specific)
- UC3: Example can be replayed step by step

### Acceptance Criteria
- AC1: Architecture doc with Mermaid diagram
- AC2: Protocol doc with JSON payload examples
- AC3: Example uses realistic data (no dummy tokens/secrets)

### Tests
- N/A (documentation)

### Docs / Comments
- Diagrams in Mermaid; render as PNG for portability
- English only (project convention)

---

## Progress

| Task | Status |
|---|---|
| CE-001 | **DONE** — scaffold, 5/5 tests |
| CE-002 | **DONE** — indexer, 18,167 chunks reales |
| CE-003 | **DONE** — retriever+injector, citas+recency |
| CE-004 | **DONE** — judge, 10/10 tests |
| CE-005 | **DONE** — advisor, 4/4 tests |
| CE-006 | **DONE** — loop worker, 9/9 tests |
| CE-007 | **DONE** — engine+CLI, 10/10 tests |
| CE-008 | **DONE** — docs + walkthrough |
| CE-009 | **DONE** — watchdog ce watch, 8/8 tests |
| CE-010 | **DONE** — adapters Claude/Codex/Gemini, 4/4 tests |

---

## TASK: CE-009 — Watchdog: continuous supervision

### Title
Live watchdog that monitors the worker session and acts on its own

### Description
Poll the worker's session DB, classify recent turns with the Judge and act on signals: repeated failures → recall + advisor + alert; uncertain → recall + advisor; idle → wake; obvious_ask → auto_answer.

### Use Cases
- UC1: worker fails 3x while unattended → watchdog recalls context and consults advisor
- UC2: worker idle > timeout → wake prompt via webhook
- UC3: supervisor mode for CI / cron

### Acceptance Criteria
- AC1: `ce watch --once` scans the live session and returns an action
- AC2: consecutive failures escalate retry → advisor → escalate
- AC3: idle detection triggers wake with webhook

### Tests
- 8/8 in tests/test_watchdog.py (no_session, ok, wake, failure+recall, uncertain, obvious_ask, webhook, escalate)

### Status
- **DONE**

---

## TASK: CE-010 — Adapters for Claude Code / Codex / Gemini CLI

### Title
Executable glue so other agents use the engine with the same protocol

### Description
Thin scripts/hooks per agent: Claude Code hooks (pre_tool_use/Stop) that call the judge; TODO.md as the work contract; recall/advisor via the ce CLI. Same protocol, different glue.

### Status
- **DONE**

---

## TASK: CE-011 — LLM as a judge (second pass)

### Title
LLM judge for ambiguous turns — the dual-judge cycle

### Description
Heuristic markers classify turns cheaply; when a turn is ambiguous (long reply, open question, mixed signals), the LLM judge classifies it with a structured JSON verdict. Degrades to heuristics on any failure. Completes the dual-judge thesis: LLM as a judge (hot path) + human as a judge (end of cycle).

### Acceptance Criteria
- AC1: LLM judge returns Verdict(source="llm") on ambiguous turns
- AC2: short clear turns never call the LLM (cost guard)
- AC3: any LLM failure degrades to heuristic verdict, never blocks the worker
- AC4: judge.llm_enabled toggle in config

### Tests
- 7/7 in tests/test_llm_judge.py (classify, bad JSON, no client, long turn, short turn skip, disabled, open question)

### Status
- **DONE** — demo real con gpt-4o: turno ambiguo -> uncertain + sugerencia concreta

---

## TASK: CE-012 — DeepSeek + loop supervisado con iteraciones

### Title
DeepSeek by default; configurable auto-iterations with token budget; injection as a user message

### Description
- Advisor/judge apuntan a DeepSeek (api.deepseek.com/v1 + DEEPSEEK_API_KEY), cero GPT
- loop.max_iterations: cuántas veces la conciencia continúa el turno del worker
- loop.max_tokens_per_task: presupuesto de tokens por tarea (0 = sin límite)
- Inyección via gateway webhook = como si el usuario escribiera (misma ruta)
- Escalada: recall → advisor (DeepSeek, sesión nueva con TODO el contexto) → si nada funciona, marcar task blocked con notas y seguir

### Acceptance Criteria
- AC1: advisor/judge usan DeepSeek por defecto (configurable)
- AC2: `ce supervise on|off|status|tick` con --max-iterations y --max-tokens
- AC3: al agotar iteraciones/tokens el loop se detiene y devuelve control al usuario
- AC4: fallo no resuelto por el advisor → task marcada blocked con notas

### Tests
- 7/7 en tests/test_supervised.py + 74/74 total
- Demo real DeepSeek: advisor respondió con alternativas y recomendación accionable

### Status
- **DONE**

---

## TASK: CE-013 — Panel de configuracion local

### Title
Local configuration panel for the engine (ce panel)

### Description
Zero-dependency web panel (stdlib http.server, loopback only) to configure the engine without touching files: API key, models, loop iterations, token budget, thresholds — plus live status, supervision controls and indexing.

### Use Cases
- UC1: first-time setup with Claude Code — drop the API key in the panel
- UC2: tune max_iterations / max_tokens per task without editing YAML
- UC3: start/stop the supervised loop and see live engine state

### Acceptance Criteria
- AC1: `ce panel` serves on 127.0.0.1 (loopback only)
- AC2: API key saved to config/local.yaml (gitignored), never returned in plain text
- AC3: config updates persisted; status shows real engine state
- AC4: supervise on/off/tick and index exposed

### Tests
- 9/9 in tests/test_panel.py (HTML, status, masked key, save key, save config, supervise, index, mask, nested apply)

### Status
- **DONE** — panel vivo en http://127.0.0.1:8899

---

## TASK: CE-014 — Memoria selectiva (RAG curado)

### Title
Selective RAG: saved memory + user prompts + agent replies (no tool dumps)

### Description
El RAG indexa solo lo que importa: prompts del usuario/conciencia, respuestas propias del agente, y la memoria que el agente decide guardar (ce save). Los tool outputs no se indexan (recall.index_tools=false). La memoria curada pesa mas en el ranking (memory_boost=1.5).

### Use Cases
- UC1: el agente termina una tarea y guarda la leccion (ce save)
- UC2: la conciencia recuerda decisiones pasadas antes de escalar
- UC3: RAG limpio sin ruido de tool dumps

### Acceptance Criteria
- AC1: ce save persiste en saved_memory + embeddings
- AC2: el retriever combina historial + memoria con boost
- AC3: chunk_from_row descarta tool outputs por defecto
- AC4: ce memory lista la memoria curada

### Tests
- 7/7 en tests/test_memory.py + 90/90 total

### Status
- **DONE** — indice depurado: 10,125 tool-chunks eliminados, 8,414 chunks de prompts+respuestas + memoria curada

---

## TASK: CE-015 — MCP server: el RAG como tool del agente

### Title
Expose the engine as MCP tools (ce_memory_search / save / judge / status)

### Description
El RAG funciona como un tool tipo search que el agente llama cuando necesita recordar (MCP, Model Context Protocol). Cualquier agente MCP-capable (Claude Code, Hermes, Codex) consume los tools via stdio JSON-RPC. Cero dependencias — protocolo implementado a mano.

### Acceptance Criteria
- AC1: handshake initialize + tools/list + tools/call sobre stdio (framing LSP)
- AC2: ce_memory_search devuelve chunks con citas
- AC3: ce_memory_save persiste memoria curada
- AC4: ce_judge y ce_status operativos
- AC5: .mcp.json para Claude Code + mcp_servers para Hermes

### Tests
- 9/9 en tests/test_mcp.py + demo real end-to-end sobre stdio

### Status
- **DONE** — conectado a Hermes (config.yaml mcp_servers dorsha-ce) y .mcp.json para Claude Code

---

## TASK: CE-016 — MCP over HTTP (la opción SaaS)

### Title
Same engine, URL transport — mcp.devcristobalvc.com ready

### Description
El mismo McpEngine (JSON-RPC) servido por HTTP: POST / para initialize/tools/list/tools/call, GET /health, auth Bearer opcional. Esto hace viable la 3a vía de distribución (subdominio gestionado) sin duplicar código: stdio y HTTP comparten el 100% del núcleo.

### Acceptance Criteria
- AC1: POST / responde JSON-RPC (initialize, tools/list, tools/call)
- AC2: GET /health reporta chunks + memoria
- AC3: --token obligatorio si host no es loopback (401 sin Bearer)
- AC4: ce mcp-http --port --host --token

### Tests
- 5/5 en tests/test_mcp_http.py + demo real (health 8,443 chunks, 401 sin token, search con token)

### Status
- **DONE**

---

## Blocked Tasks

_None yet. Tasks move here after 3+ failed attempts trigger the Advisor._

---

## Done

_Archived snapshots in done/_ (titles + verification evidence only).
