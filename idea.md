# Idea — Dorsha Consciousness Engine

## Vision

A universal **consciousness layer** for any AI agent or agentic system — Claude Code, Codex, Gemini CLI, Hermes, or any custom agent framework.

Not a framework replacement. Not a runtime. A **thin, file-based protocol + supervision service** that gives any agent three abilities it otherwise lacks:

1. **Recall** — remember what escapes its context window (RAG over full history)
2. **Judgment** — notice when it is wrong, stuck or unsure, and correct course
3. **Refresh** — get woken up and re-focused when it lags, wanders or stalls

The engine works **beside** the agent, as a project manager would work beside an engineer: it watches, it reminds, it flags, it escalates — it never does the work itself.

## The problem

Agents fail silently. They:

- **Lose context** — the window fills, and decisions made 3 sessions ago are gone
- **Wander** — drift away from the acceptance criteria into unbounded exploration
- **Repeat errors** — retry the same broken approach N times without learning
- **Stall** — go idle waiting for input that was already answered, or that no one knows they need

Without supervision, each failure costs human attention. With supervision, the human only ever sees the **real** decisions.

## Core concept: the TODO.md contract

The working contract between the agent and its consciousness is a structured markdown block — one file per work cycle, one task per section:

```markdown
## TASK: <ID> — <short title>

### Title
<what the task is>

### Description
<what to build / what problem it solves>

### Use Cases
- UC1: <who does what, when>
- UC2: ...

### Acceptance Criteria
- AC1: <verifiable statement>
- AC2: ...

### Tests
- <how to verify each AC — commands, checks>

### Status
- `in_progress` | `completed` | `blocked`

### Limitation
- (only when blocked) <what blocks the task, what was tried, what's needed>
```

### Status flow

```
in_progress ──tests pass──▶ completed
     │
     └──real limitation──▶ blocked (+ limitation comment) ──human resolves──▶ next cycle
```

**Rules of the contract:**

- The agent **never stays stuck**: a real limitation is marked `blocked` with an honest comment, and the agent **moves to the next task**
- `completed` requires **verified tests / acceptance criteria** — not "looks done"
- When the cycle ends, the **human architect** reviews only the `blocked` items and the open decisions — that is the entire human surface
- The agent may ask the human **only** about real limitations, after exhausting automatic paths (retries, advisor, recall)

## The consciousness as project manager

The engine supervises the agent against the TODO.md contract and intervenes on three signals:

| Signal | Detection | Refresh action |
|---|---|---|
| **Lagging** | no activity > T minutes | inject wake prompt with last exchange + TODO state (loop driver) |
| **Wandering** | output drifts from acceptance criteria / current task | re-inject the ACs + relevant history (recall + judge) |
| **Repeating errors** | N consecutive failures on the same task | recall context → advisor consult → course correction → escalate if still blocked |

The engine also handles the agent's **memory blind spot**: on `context_exhausted` or `uncertain_choice`, it retrieves from the full session history (RAG) and injects cited excerpts — so the agent reasons with what it *did*, not only what it *remembers*.

## Human surface (by design, minimal)

1. Resolve `blocked` tasks at the end of a cycle (decision, resource, external dependency)
2. Review advisor escalations (rare: agent failed, advisor failed, human decides)
3. Define new features → new TODO.md blocks

Everything else is automatic. The human is the architect, not the babysitter.

## Agent-agnostic by design

- **Protocol is files**: `TODO.md` (work contract), `idea.md` (this), status fields — any agent that can read and write markdown can participate
- **Events are transport-agnostic**: CLI, webhook, or stdin — the engine doesn't care what the agent is
- **Adapters per agent**: thin docs + scripts for Claude Code, Codex, Gemini CLI, Hermes — same protocol, different glue
- **Models are swappable**: worker, advisor, embeddings all configured, never hardcoded

## Principles

1. Human escalates **only real limitations** — never ask yes/no when the answer is obvious
2. `completed` = verified, not assumed
3. Honest `blocked` beats silent drift
4. No secrets in files, no PII in examples
5. Configurable thresholds (attempts, idle time, confidence floor)
6. English-only, protocol-first, framework-last

## The dual judge (core thesis)

> "La conciencia es un LLM as a judge y al mismo tiempo human in the loop
> human as a judge." — C. Valencia, 2026

The engine is a dual-judge system:

| Judge | Scope | When |
|---|---|---|
| **LLM as a judge** | every turn, hot path | heuristic markers first (cheap); the LLM judge classifies ambiguous turns (long rambling replies, open questions) with a structured JSON verdict |
| **Human as a judge** | end of cycle only | resolves real limitations (`blocked`) and escalation decisions — the entire human surface |

The LLM judge never blocks the worker: on failure it degrades to the
heuristic verdict. The human judge is never asked yes/no questions.

## What success looks like

An agent with the engine attached ships a feature from a TODO.md block **without a single human prompt** — it recalls past decisions, catches its own errors, stays on task, and at the end the human reviews only the 2–3 real decisions it surfaced. The same protocol works identically in Claude Code, Codex, Gemini CLI and any future agent.
