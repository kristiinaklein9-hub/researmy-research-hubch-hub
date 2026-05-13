# Agentic workflow reference — 9 elements × research-hub × LangGraph

> Purpose: A study/build reference for designing the **next**
> multi-system AI workflow framework after research-hub.
> Maps universal agentic-system primitives to (a) research-hub's
> concrete implementation and (b) LangGraph's named equivalents,
> plus Anthropic's [6 effective-agent patterns](https://www.anthropic.com/research/building-effective-agents).
>
> Audience: future-Wenyu or future collaborator who has internalized
> research-hub and now wants to build something analogous in a
> different domain (e.g. `market-data → analyst-report`, or
> `lab-notebook → manuscript`).

## 1. Three categories that get confused

| Category | Role | Examples |
|---|---|---|
| **Harness** | Runtime that hosts agents — tool routing, context window, loop control, permission gates | Claude Code, LangGraph runtime, AutoGen runtime, Codex CLI |
| **Agentic workflow** | DAG-like pipeline of stages where each stage MAY call an LLM; humans pre-define the stage order | research-hub, n8n with AI nodes, Anthropic's "prompt chaining" pattern |
| **Autonomous agent** | LLM itself picks the next tool call; stage order is emergent | Claude in chat, SWE-agent, Devin, Voyager |

research-hub sits in the middle bucket. Same primitives apply to all
three, but proportions differ: harnesses lean on **loop control +
permission**, workflows lean on **schema + idempotency**, autonomous
agents lean on **planning + memory**.

## 2. The 9 universal elements

For any multi-step LLM-driven system, you need all 9. Skip one and
you'll feel the pain at a specific lifecycle moment.

| # | Element | What breaks if missing |
|---|---|---|
| 1 | **State persistence** | Re-running re-does work; cross-session memory lost |
| 2 | **Tool / Adapter layer** | Each integration becomes bespoke spaghetti |
| 3 | **Planning / control flow** | Nondeterminism creeps in; can't reason about behavior |
| 4 | **Memory hierarchy** (turn / session / long-term) | Either context blows up or facts get re-derived every run |
| 5 | **Idempotency + retry semantics** | Transient failure becomes permanent corruption |
| 6 | **Observability** (logs / traces / audit) | Debugging blind; regressions ship silently |
| 7 | **Safety boundaries** (permission tiers, sandbox, kill switch) | One bad LLM output costs real money or data |
| 8 | **Human-in-the-loop checkpoints** | Either fully manual (no leverage) or fully autonomous (no recourse) |
| 9 | **Schema contracts** | Each layer interprets data differently; integration drift |

## 3. Three-way reference table

For each element: research-hub's concrete file path + LangGraph's
named equivalent + the Anthropic pattern it enables.

### 1 — State persistence

| Lens | Implementation |
|---|---|
| **research-hub** | `<vault>/.research_hub/*.json` sidecars: `clusters.json`, `dedup_index.json`, `nlm_cache.json`, `verify_cache.json`, `zotero_kept_collections.json`. Per-cluster: `hub/<slug>/.fit_check_accepted.json`, `.ingest_gap.json`, `.ingest_validation.json`. Atomic via `tmp.replace(path)`. |
| **LangGraph** | `Checkpointer` (in-memory / SQLite / Postgres). `StateGraph.compile(checkpointer=...)` makes state persist between `invoke()` calls. |
| **Anthropic pattern** | Enables *Evaluator-optimizer* (each iteration's verdict survives) and *Autonomous agent* (resumable long runs). |

### 2 — Tool / Adapter layer

| Lens | Implementation |
|---|---|
| **research-hub** | One module per external system. `zotero/client.py` wraps pyzotero with retry + error mapping; `notebooklm/client.py` wraps notebooklm-py's async RPC behind a sync facade; `notebooklm/upload.py` wraps cluster-level operations on top of the client. **Rule**: external errors get re-raised as our own `NotebookLMError` / `ZoteroError`. |
| **LangGraph** | `@tool` decorator + `ToolNode` + `bind_tools()`. Type signature is the contract; LangGraph synthesizes the function-calling schema. |
| **Anthropic pattern** | Foundation of *Augmented LLM* — every other pattern composes tools. |

### 3 — Planning / control flow

| Lens | Implementation |
|---|---|
| **research-hub** | Hardcoded stage order via CLI subcommands (`discover new → continue → ingest → bundle → upload → generate → download`). pipeline.py's `run_pipeline()` calls each stage in sequence. No LLM-decided routing yet. |
| **LangGraph** | `add_edge(start, end)` for static transitions; `add_conditional_edges(node, router_fn)` for LLM-or-code routed branches. |
| **Anthropic pattern** | Enables *Prompt chaining* (static order), *Routing* (conditional branches), *Orchestrator-workers* (central planner node). |

### 4 — Memory hierarchy

| Layer | research-hub | LangGraph |
|---|---|---|
| Turn-local | function args | reducer-updated `state` keys |
| Session/cache | `dedup_index.json`, `verify_cache.json`, `nlm_cache.json` | thread-scoped `Checkpointer` state |
| Long-term | Zotero library (truth store), Obsidian vault, `hub/<slug>/memory.json` (per-cluster registry), `.notebooklm/profiles/default/storage_state.json` (auth) | external store the user wires (Mem0, Zep, pinecone, files) |

**Anthropic pattern**: long-term memory enables *Autonomous agent*
across sessions; cache memory enables idempotent *Prompt chaining*.

### 5 — Idempotency + retry semantics

| Lens | Implementation |
|---|---|
| **research-hub** | `dedup_index.json` keyed by DOI + title hash — re-running ingest is a no-op for already-known papers. `verify_cache.json` skips already-verified DOIs. `nlm_cache.json` records `uploaded_doi_count` so `upload --skip-uploaded` is correct. Each stage writes its sidecar atomically (`tmp.replace`). |
| **LangGraph** | `Checkpointer` + `interrupt_before/after` lets a run resume exactly where it stopped. State reducers (e.g. `add_messages`) are explicitly designed to be re-application-safe. |
| **Anthropic pattern** | Required for *Evaluator-optimizer* loops (retry with feedback) and any long-running *Autonomous agent*. |

### 6 — Observability

| Lens | Implementation |
|---|---|
| **research-hub** | JSONL log lines in `<vault>/.research_hub/logs/<date>/`. `doctor` health check runs ~25 invariant probes. Per-cluster `.ingest_validation.json` / `.ingest_gap.json` sidecars are read-only audit artifacts. `Agent A`-style audit scripts in `.ai/audit_*.py` can re-derive the entire state without writing. |
| **LangGraph** | `LangSmith` traces (every node call), `with_config({"callbacks": [...]})`. Built-in tracing of state mutations. |
| **Anthropic pattern** | Universal. Without it you can't trust any pattern in production. |

### 7 — Safety boundaries

| Lens | Implementation |
|---|---|
| **research-hub** | `--dry-run` on every destructive command. `zotero gc --apply --yes` only auto-confirms the (empty + test-pattern + orphan) trifecta; everything else prompts. `mark-kept` is the explicit "do not delete this" gesture. Default `--respect-kept` ON. |
| **LangGraph** | `interrupt_before=["delete_node"]` + `Command(resume=...)` for human approval. `RunnableConfig.recursion_limit` as a runaway-loop kill switch. |
| **Claude Code analogue** | Permission mode (`default` / `acceptEdits` / `plan` / `bypassPermissions`), `request_access` tiers (`read` / `click` / `full`). |
| **Anthropic pattern** | Required for any *Autonomous agent* in production; required for *Orchestrator-workers* when workers can write. |

### 8 — Human-in-the-loop checkpoints

| Lens | Implementation |
|---|---|
| **research-hub** | The flagship example is `discover new → AI scores → discover continue`: search emits a fit-check prompt + state stash, you (or Claude) score externally, paste back, ingest proceeds. Also: `paper attach-pdfs --batch` interactive prompt, `clusters delete` confirmation. |
| **LangGraph** | `graph.compile(interrupt_before=["sensitive_step"])` + `graph.update_state(...)` to inject human edits. |
| **Anthropic pattern** | The "checkpoint" between *Orchestrator* output and *Worker* execution; the "review" in *Evaluator-optimizer*. |

### 9 — Schema contracts

| Lens | Implementation |
|---|---|
| **research-hub** | dataclasses everywhere (`Cluster`, `PaperRecord`, `FitCheckResult`, `GapEntry`, `PdfAttachEntry`, `DownloadReport`). Sidecar JSON shapes are documented in module docstrings; the same shape is read in tests + audit scripts. Frontmatter spec in `vault/link_updater.py` + `zotero/fetch.py:make_raw_md`. |
| **LangGraph** | `TypedDict` or Pydantic `BaseModel` as the graph state type, enforced at every node boundary. |
| **Anthropic pattern** | Without typed contracts, *Orchestrator-workers* and *Routing* both degrade — the orchestrator can't validate worker output, the router can't reliably classify. |

## 4. Anthropic's 6 patterns ↔ research-hub correspondences

[Source: Building effective agents (2024)](https://www.anthropic.com/research/building-effective-agents)

| Pattern | research-hub instance | Status |
|---|---|---|
| **Augmented LLM** (single LLM + tool use) | Every individual step that calls Claude: fit-check scoring, paper-summarize, autofill | Live |
| **Prompt chaining** (sequential, deterministic LLM calls) | `discover new → discover continue → ingest`. Each step's output is the next step's input file. | Live |
| **Routing** (classifier picks the branch) | `notebooklm download --type {brief, slide-deck}` (today: arg-driven, not LLM-driven). `zotero gc` skip-vs-include logic via `kept_keys`. | Partial |
| **Parallelization** (fan-out, aggregate) | The 4-agent audit (Zotero / Obsidian / NLM / CI fix) that produced v0.87 was this pattern, run via Claude Code's parallel `Agent` tool calls. | Live (in the build process, not yet in research-hub runtime) |
| **Orchestrator-workers** (planner + delegated workers) | pipeline.py is a passive orchestrator; the workers are zotero/obsidian/notebooklm subsystems. Stronger version: codex-delegate / gemini-delegate from CLAUDE.md. | Partial |
| **Evaluator-optimizer** (iterate with feedback) | N1 validator + Z1 PDF reporter only WARN today; they don't auto-retry. **Promotion path for v0.88**: validator detects bad source → re-fetch URL automatically. | Roadmap |
| **Autonomous agent** (LLM loop) | Not in research-hub itself — the `research-hub` Skill in `~/.claude/skills/research-hub/SKILL.md` is the entry point for an autonomous Claude to drive the pipeline. | Out-of-scope by design |

**Design intent**: research-hub is deliberately on the *deterministic*
side of the autonomy spectrum. The truth store is Zotero, the unit
of work is a cluster, and the user's intent is captured in
`papers_input.json` before any LLM runs. We use LLMs **inside** steps,
not to **choose** steps. That makes it auditable.

## 5. Build a minimal harness yourself (recipe)

To internalize the 9 elements, build a 200-line harness with all of
them. Domain doesn't matter — pick anything with ≥ 2 external systems
(e.g. GitHub issues + Slack notifications, or Gmail + Calendar).

```
my-harness/
  state/                       # element 1, 4
    sessions/<id>.json
    cache.json
  adapters/                    # element 2
    github.py
    slack.py
  pipeline/                    # element 3
    plan.py    (or graph.py if you use LangGraph)
  models.py                    # element 9 — TypedDict or dataclass schema
  retry.py                     # element 5
  logger.py                    # element 6 — JSONL line per tool call
  permissions.py               # element 7
  checkpoints.py               # element 8
  cli.py                       # entrypoint (argparse)
  tests/
    test_pipeline.py
  README.md
```

**Lifecycle drill**: write each element in isolation, then write a
test that triggers its failure mode (state corruption, network
timeout, schema violation). The recovery path you have to write
proves you actually have the element.

## 6. Reading / studying list (in order)

**Tier 1 — read this week**
1. [Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents) — the canonical 6-pattern reference
2. [LangGraph Concepts: low-level architecture](https://langchain-ai.github.io/langgraph/concepts/low_level/) — `StateGraph`, reducers, checkpointers
3. [Claude Code documentation](https://docs.claude.com/en/docs/claude-code/overview) — permissions, hooks, skills, plugins (this is a harness with all 9 elements, well documented)
4. `~/.claude/CLAUDE.md` — re-read your own global guidelines through the 9-element lens; you'll spot which ones you've already internalized

**Tier 2 — read next month**
1. ReAct (Yao et al. 2022) — agentic reasoning + tool use baseline
2. Reflexion (Shinn et al. 2023) — self-correction loop (Evaluator-optimizer in paper form)
3. Voyager (Wang et al. 2023) — long-horizon autonomous agent in Minecraft, strong on memory hierarchy
4. Toolformer (Schick et al. 2023) — how LLMs learn to call tools

**Tier 3 — comparative reading (pick any 3, juxtapose)**
- Harnesses: Claude Code, Cursor, Aider, Codex CLI, SWE-agent
- Orchestrators: LangGraph, AutoGen, CrewAI, Temporal
- Pure agents: BabyAGI, AutoGPT (historical), Devin (architecture posts)

## 7. Building research-hub-2: a checklist

When you start the next framework, walk through this:

- [ ] What is the **truth store**? (Zotero in v1. For market-data: a parquet file? a SQLite DB?)
- [ ] What are the **stages** and their order? (write it down BEFORE coding)
- [ ] What is the **schema** for each stage's input/output? (start with dataclasses)
- [ ] For each external system, what's the **adapter** boundary and error mapping?
- [ ] How does each stage achieve **idempotency**? What's the dedup key?
- [ ] What gets **cached** (transient) vs **persisted** (durable)? Where?
- [ ] What's the **observability** unit? (one JSONL line per tool call, one sidecar per stage?)
- [ ] Where are the **safety gates** — dry-run, --yes, permission tiers?
- [ ] Where is the **human-in-the-loop** checkpoint? (you should be able to point at exactly one — if zero, you've over-automated; if more than two, you've under-automated)
- [ ] What surfaces does the workflow expose? (CLI? MCP? REST? Skill?)
- [ ] What does `doctor` check? (write it FIRST, run it before every commit)
- [ ] What's the **regression baseline** artifact? (`audit_*.json` like agent A left us)

If you can answer all 12 before writing the first line of code, the
framework will be reusable. If not, you'll rebuild it twice anyway.

---

*Maintained by Wenyu. Last updated: v0.87.0 (2026-05-13).*
*If LangGraph or Anthropic's terminology changes, re-derive the
mapping rather than blindly updating — the elements (§2) are
invariant; only the labels move.*
