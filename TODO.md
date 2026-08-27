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

## Blocked Tasks

_None yet. Tasks move here after 3+ failed attempts trigger the Advisor._

---

## Done

_Archived snapshots in done/_ (titles + verification evidence only).
