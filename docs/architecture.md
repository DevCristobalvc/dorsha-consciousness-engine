# Architecture

## Overview

Dorsha Consciousness Engine is a supervision layer that sits beside any AI agent. It is transport-agnostic: the same protocol works for Hermes, Claude Code, Codex, Gemini CLI or a custom framework.

```
                         ┌──────────────────────────────────────────────┐
                         │                  WORKER AGENT                │
                         │  executes tasks · calls tools · responds    │
                         └───────┬──────────────────────────┬──────────┘
                                 │ events                    │ actions
                                 ▼                           ▼
┌────────────────────────────────────────────────────────────────────────┐
│                            ENGINE (core.py)                            │
│                                                                        │
│  ┌───────────────┐   ┌────────────────┐   ┌─────────────────────────┐  │
│  │  RECALL       │   │  JUDGE         │   │  LOOP DRIVER            │  │
│  │  retriever.py │   │  detector.py   │   │  todowatcher.py         │  │
│  │  injector.py  │   │  router.py     │   │  idle.py                │  │
│  └──────┬────────┘   └───────┬────────┘   │  worker.py              │  │
│         │                    │            └────────────┬────────────┘  │
│         │              ┌─────▼─────┐                   │               │
│         │              │ ADVISOR   │  (reasoning only, │               │
│         │              │ advisor.py│   no tools)       │               │
│         │              └─────┬─────┘                   │               │
│  ┌──────▼──────┐             │                         │               │
│  │ vector store│             ▼                         ▼               │
│  │ sqlite-vec  │        escalate → human          wake / next-task     │
│  └─────────────┘                                                   │   │
└────────────────────────────────────────────────────────────────────│───┘
                                                                     │
                                                  ┌──────────────────▼───┐
                                                  │  TODO.md (contract) │
                                                  └──────────────────────┘
```

## Components

| Module | Responsibility | Key classes |
|---|---|---|
| `engine/recall/indexer.py` | Read session DB, embed, store vectors | `Indexer` |
| `engine/recall/retriever.py` | kNN + recency decay scoring | `Retriever`, `RetrievedChunk` |
| `engine/recall/injector.py` | Format recall block with citations | `Injector` |
| `engine/judge/detector.py` | Classify turns: ok/failed/uncertain/obvious_ask | `JudgeDetector`, `Verdict` |
| `engine/judge/router.py` | retry → advisor → escalate | `JudgeRouter`, `Decision` |
| `engine/advisor/advisor.py` | Reasoning-only second opinion (JSON) | `Advisor`, `AdvisorBrief` |
| `engine/loop/todowatcher.py` | Parse the work contract | `TodoWatcher`, `Task` |
| `engine/loop/idle.py` | Idle detection | `IdleDetector` |
| `engine/loop/worker.py` | Pulse: wake / next-task / blocked | `LoopWorker`, `LoopAction` |
| `engine/core.py` | Orchestration facade | `Engine` |
| `engine/cli.py` | `ce` command line | `main` |

## Scoring

```
recall score = cosine × recency_decay
cosine       = 1 − (squared_euclidean / 2)     # unit vectors, sqlite-vec
recency_decay = exp(−age / (half_life_days × 86400))
```

## Storage

- Session history: `settings.session_db` (read-only; the agent's own DB, outside this repo)
- Vector index: `settings.vector_store/recall.vec.db` — `vec0` virtual table + `meta` table (provenance)
- Work contract: `TODO.md` (project root or `--todo`)

## Data flow (blocked worker)

1. Worker fails N times → emits `attempt_failed`
2. `Engine.judge()` → `Decision.retry` (attempts < max) or `Decision.advisor`
3. `Engine.advise(brief)` → advisor returns alternatives + recommendation (JSON)
4. Worker retries with advisor's path
5. Still blocked → `Decision.escalate` → human resolves (only real limitations)
