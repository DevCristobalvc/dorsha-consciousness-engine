# Walkthrough: a blocked worker, end to end

This is the canonical example of the engine at work. It mirrors the unit tests
and the real demo against the session history (18k+ chunks indexed).

## Setup

```bash
pip install -e ".[dev,recall]"
cp config/config.example.yaml config/local.yaml   # point session_db at your agent's DB
ce index                                          # one-time embedding index
```

## Step 1 — the worker hits a wall

The worker agent tries to deploy, fails 3 times:

```text
attempt 1: deploy failed with error 500
attempt 2: deploy failed with error 500
attempt 3: deploy failed with error 500
```

## Step 2 — judge classifies

```bash
ce judge "deploy failed with error 500" --task deploy-001
# action: retry | reason: attempt 1/3 | attempts: 1
```

After the third failure:

```bash
ce judge "deploy failed with error 500" --task deploy-001
# action: advisor | reason: max attempts reached, consulting advisor
```

## Step 3 — recall refreshes memory first

```bash
ce recall "deploy vercel token" --k 2
# [RECALL — retrieved from session history]
# (cron_... msg:6064 @ 2026-07-22 03:07 UTC) assistant: "Wait — vercel whoami returns devcristobal! ..."
# (cron_... msg:8595 @ 2026-07-22 07:57 UTC) assistant: "No Vercel token available, but the push should trigger auto-deploy..."
```

The worker now knows what was decided about this exact problem weeks ago.

## Step 4 — advisor offers alternatives

```python
from engine.config import Settings
from engine.core import Engine
from engine.advisor.advisor import AdvisorBrief

e = Engine(Settings.from_yaml("config/local.yaml"))
advice = e.advise(AdvisorBrief(
    problem="deploy fails with 500 on Vercel",
    attempts=["redeployed 3 times", "checked logs via dashboard"],
    hypothesis="env var missing at build time",
    evidence="error surfaces only in production build",
))
# alternatives: ["verify NEXT_PUBLIC_* at build", "inspect build logs", "rollback to last good deploy"]
```

## Step 5 — retry with the advisor's path

Worker verifies the build-time env var → finds it missing → fixes it → deploys
→ tests pass → marks the task `completed` in `TODO.md`.

## Step 6 — still blocked? escalate, never ask yes/no

If the worker fails again after the advisor's path:

```bash
ce judge "still failing after advisor fix" --task deploy-001
# action: escalate | reason: advisor already consulted, escalating to human
```

The human architect sees exactly one decision: the real limitation. That is
the entire human surface of the cycle.

## Step 7 — loop status

```bash
ce loop status
# next: CE-008 | blocked: [] | chunks: 18167
```

When every task is `completed` and nothing is `blocked`, the cycle is done:

```bash
ce status | grep todo_next   # todo_next: None  → all_done
```
