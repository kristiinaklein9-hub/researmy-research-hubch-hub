# `.coord/multi_ai_plan.md` Template

Drop this template at `.coord/multi_ai_plan.md` (or `.coord/multi_ai_plan_<slug>.md` if a plan already lives at the default name) and fill in the fields. Do not invent fields — the schema is enforced by reconcilers.

```yaml
---
plan_id: <short-slug-2026-05-09>
created_utc: 2026-05-09T00:00:00Z
goal: |
  One paragraph. What does this multi-AI round accomplish that a single delegate cannot?
  Why split now rather than sequence later?

success_criteria:
  - <observable check that the whole round succeeded — e.g. "tests pass and zh-TW companion exists">
  - <second check if relevant>

tasks:
  - id: t1
    agent: codex
    brief_path: .ai/codex_task_t1.md
    depends_on: []
    success_criteria:
      - <e.g. "pytest -q passes in tests/test_module.py">
      - <e.g. "result.json status == success">

  - id: t2
    agent: gemini
    brief_path: .ai/gemini_task_t2.md
    depends_on: [t1]
    success_criteria:
      - <e.g. "docs/feature.zh-TW.md exists with same heading count as docs/feature.md">

  - id: t3
    agent: claude
    brief_path: inline       # for Claude tasks, brief lives in this file
    depends_on: [t1, t2]
    success_criteria:
      - <e.g. "PR description references both t1 and t2 outputs">

risks:
  - <known risk that Claude must watch for during reconciliation>

reconciliation:
  agent: claude
  steps:
    - Read each leaf's result.json and compare with success_criteria.
    - On mismatch, append a fix-up task with a fresh id; do not mutate existing tasks.
    - Once all tasks pass success_criteria, mark plan as done with a `done_utc` field.
---

# Brief: t3 (claude, inline)

(Free-form per-task brief for tasks where `brief_path: inline`. For tasks with a real brief path, leave this section out — the leaf reads the brief from its own file.)

## Context
- ...

## Goal
- ...

## Constraints
- Stay within the success_criteria above.
- Do not silently widen scope.

## Acceptance
- ...
```

## Field reference

| Field | Required | Notes |
|---|---|---|
| `plan_id` | yes | Short slug, append date if multiple plans land same week |
| `created_utc` | yes | ISO 8601 UTC |
| `goal` | yes | One paragraph; this drives accept/reject judgment downstream |
| `success_criteria` | yes | Round-level. Per-task criteria live under each task |
| `tasks[].id` | yes | Unique within the plan; referenced in `depends_on` |
| `tasks[].agent` | yes | One of `codex`, `gemini`, `claude` |
| `tasks[].brief_path` | yes | Path to the task brief file. Use `inline` for Claude tasks whose brief lives below the YAML in this same file |
| `tasks[].depends_on` | yes | List of task ids; can be empty |
| `tasks[].success_criteria` | yes | Verifiable assertions or commands |
| `risks` | optional | Free-form notes for reconciler |
| `reconciliation` | optional | Defaults to `agent: claude` if absent |

## Conventions

- One plan per round of work. If the round expands, append `_v2` to the plan_id and create a sibling file rather than editing in place.
- Briefs at `brief_path` follow the per-leaf-skill conventions (see `codex-delegate` and `gemini-delegate` for their respective brief shapes).
- Every leaf must emit a machine-readable `result.json` so the reconciler can check `success_criteria` programmatically.

## Anti-patterns

- A plan with one task: use the leaf skill directly, skip the router.
- A plan whose tasks all have the same agent and no dependencies: use the leaf's parallel-execution pattern, skip the router.
- Mutating an existing task's `success_criteria` after a leaf finished: append a fix-up task instead.
