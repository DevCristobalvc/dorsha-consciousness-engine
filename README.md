# Dorsha Consciousness Engine

> The meta-agent "consciousness" layer for **Dorsha** — an autonomous AI agent: it **remembers** what escapes the context window, **judges** when the agent is wrong or uncertain, and **wakes it up** when it stalls.

## What it does

An autonomous agent (the "worker") executes tasks with tools. The **Consciousness Engine** sits on top of it as a second-order layer with three responsibilities:

```
┌──────────────────────────────────────────────────────────┐
│                      WORKER AGENT                        │
│          executes tasks · calls tools · responds         │
└──────────────┬──────────────────────┬────────────────────┘
               │                      │
     ┌─────────▼─────────┐   ┌────────▼─────────┐
     │  1. RECALL (RAG)  │   │  2. JUDGE        │
     │  retrieves past   │   │  detects errors & │
     │  context beyond   │   │  uncertainty      │
     │  the window       │   │  → retry / advise │
     │  → injects it     │   │  / escalate       │
     └─────────┬─────────┘   └────────┬─────────┘
               │                      │
               └──────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │  3. LOOP DRIVER       │
              │  reads TODO + last    │
              │  exchange · generates │
              │  next prompt · wakes  │
              │  idle agents          │
              └───────────────────────┘
```

### 1. Recall — RAG over session history

When the worker's context/memory window is exhausted, the engine performs retrieval-augmented recall over the **full conversation and session history** (stored in the session database) and injects the relevant fragments into the worker's context.

- Indexes: session history, TODO files, project docs
- Retrieval: embeddings + vector store (sqlite-vec / Chroma)
- Injection: relevant excerpts appended to context with provenance (session id, timestamp, message id)

### 2. Judge — error & uncertainty validation

The engine validates the worker's behavior between turns:

- **Error detection**: repeated failed attempts (N retries), tool exceptions, contradictory outputs, broken acceptance criteria
- **Uncertainty detection**: low-confidence phrasing, ambiguous choices, missing context that recall can't resolve
- **Decision path**: if retry threshold reached → consult **Advisor agent** (second opinion, no tools, reasoning-only) → if still blocked → escalate to human with full context and suggested options

### 3. Loop Driver — autonomous activation

The engine acts as the pulse of the worker:

- Reads the last user exchange + the active project's `TODO.md`
- Generates the next prompt/action for the worker
- Detects idle workers (no response after T minutes) and reactivates them via webhook
- Marks blocked tasks in the TODO with attempt count and next action

## Components

```
src/
├── recall/        # RAG pipeline: indexing, retrieval, injection
│   ├── indexer.py
│   ├── retriever.py
│   └── injector.py
├── judge/         # error/uncertainty detection + decision routing
│   ├── detector.py
│   ├── router.py
│   └── thresholds.yaml
├── loop/          # cron worker: TODO watcher, idle detection, wake
│   ├── worker.py
│   ├── todowatcher.py
│   └── idle.py
├── advisor/       # second-opinion agent (reasoning-only, no tools)
│   └── advisor.py
└── engine.py      # orchestration entrypoint
config/            # thresholds, model routing, storage paths
docs/              # architecture, protocol specs
```

## Protocol (draft)

| Event | Trigger | Action |
|---|---|---|
| `context_exhausted` | window/memory full | RAG recall → inject relevant history |
| `attempt_failed` | worker fails N times | retry → advisor consult → escalate |
| `uncertain_choice` | low confidence / ambiguity | recall again → advisor → escalate |
| `idle_timeout` | no activity > T min | inject wake prompt + webhook |
| `task_done` | acceptance criteria met | archive TODO → next task |

## Rules

- Never expose secrets (tokens, keys, PII) in logs or README examples
- Human escalation only after all automatic paths are exhausted
- All thresholds configurable (attempts, idle time, confidence floor)
- Recall must cite provenance (session/message id) for every injection

## Status

**Init** — architecture defined, repo scaffolded. See [TODO.md](TODO.md).
