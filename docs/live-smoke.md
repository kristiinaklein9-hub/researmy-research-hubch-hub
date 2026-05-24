# Live Smoke Checklist

Use this checklist before a release, after changing AI-host integration,
or before claiming a new host is verified. These checks intentionally
touch real local state and external services, so they are not part of
the default CI suite.

## Scope

| Surface | What this proves |
|---|---|
| CLI | The installed package can expose its capability manifest and run local workflows. |
| Obsidian vault | research-hub can write Markdown notes, dashboards, bases, memory, and artifacts. |
| Zotero | Credentials work and Zotero Web API writes are possible. |
| NotebookLM | Browser login, bundle/upload/generate/download still work against Google's current UI. |
| MCP | Tool-calling AI hosts can attach the same workflow through stdio. |
| REST | Browser-only or HTTP-capable assistants can call `/api/v1/*`. |
| AI CLI adapters | `auto`, summarize, crystals, and autonomous bootstrap detect the same LLM CLI registry. |

## 0. Baseline

```bash
python -m research_hub describe --json
python -m research_hub describe --filter skills --pretty
research-hub install --list
research-hub where
```

Expected:

- `describe --json` emits valid JSON.
- `install --list` lists only built-in installer targets:
  `claude-code`, `codex`, `cursor`, `gemini`.
- OpenClaw, Hermes, and other hosts are not listed as installer targets
  unless they have been explicitly implemented and tested.

## 1. Obsidian / Local-Only Smoke

This path needs no Zotero or NotebookLM account.

```bash
SMOKE_VAULT=$(mktemp -d)/research-hub-smoke
research-hub init --sample --vault "$SMOKE_VAULT"
research-hub dashboard --vault "$SMOKE_VAULT" --sample
research-hub doctor --vault "$SMOKE_VAULT"
```

On Windows PowerShell:

```powershell
$SMOKE_VAULT = Join-Path $env:TEMP "research-hub-smoke"
research-hub init --sample --vault $SMOKE_VAULT
research-hub dashboard --vault $SMOKE_VAULT --sample
research-hub doctor --vault $SMOKE_VAULT
```

Expected:

- `_HOME.md` exists.
- The dashboard renders.
- Doctor reports local vault issues clearly, not as tracebacks.

## 2. Zotero Live Smoke

Use a dedicated test collection or disposable Zotero library.

```bash
export ZOTERO_API_KEY=...
export ZOTERO_LIBRARY_ID=...
export ZOTERO_LIBRARY_TYPE=user
research-hub doctor
research-hub search "agent-based modeling LLM governance" --limit 3 --json
research-hub zotero backfill --tags --notes --dry-run
```

Expected:

- `doctor` reaches Zotero or reports an actionable credential error.
- Search returns real identifiers where available.
- Backfill dry-run does not mutate Zotero.

Live write check, only in a disposable collection:

```bash
research-hub add --doi 10.48550/arXiv.2401.00001 --cluster smoke-test
research-hub sync status
```

Expected:

- Zotero item is created or a clear duplicate/credential error is shown.
- Obsidian note contains `zotero-key` when Zotero write succeeds.

## 3. NotebookLM Live Smoke

NotebookLM remains the least stable dependency because it depends on
Google login state and browser/UI behavior.

```bash
research-hub notebooklm login
research-hub notebooklm bundle --cluster smoke-test
research-hub notebooklm upload --cluster smoke-test
research-hub notebooklm generate --cluster smoke-test --type brief
research-hub notebooklm download --cluster smoke-test
research-hub notebooklm read-briefing --cluster smoke-test
```

Expected:

- First login is human-driven in a browser.
- Upload uses the current saved session.
- Downloaded briefing is stored under `.research_hub/artifacts/`.

Known acceptable failure modes:

- Google asks for phone/device verification.
- Cookies expire and require `research-hub notebooklm login` again.
- NotebookLM UI changes and upload/generate selectors need repair.
- Publisher URLs are blocked or return error-page HTML; upload local PDFs
  manually for those sources.

## 4. MCP Host Smoke

Configure the host:

```json
{
  "mcpServers": {
    "research-hub": {
      "command": "research-hub",
      "args": ["serve"]
    }
  }
}
```

Then ask the host to call:

- `run_doctor`
- `list_clusters`
- `search_papers`
- `plan_research_workflow`
- `auto_research_topic` with `do_nlm=false` for a first smoke

Expected:

- The host sees research-hub tools after restart.
- Tool calls return structured results.
- OpenClaw, Cline, Roo Code, Continue.dev, Cursor, Claude Desktop, and
  Claude Code should all use this MCP path before any host-specific skill
  installer is claimed.

## 5. REST Smoke

Start the dashboard/API server:

```bash
research-hub serve --dashboard --api-token smoke-token
```

In another terminal:

```bash
curl http://127.0.0.1:8765/api/v1/health
curl -X POST http://127.0.0.1:8765/api/v1/plan \
  -H "Authorization: Bearer smoke-token" \
  -H "Content-Type: application/json" \
  -d "{\"intent\":\"LLM agent evaluation harness\"}"
```

Expected:

- `/api/v1/health` works without a bearer token.
- Other endpoints require the bearer token when configured.
- Binding to non-localhost without a token is rejected by the server.

## 6. AI CLI Adapter Smoke

Run these for every LLM CLI you claim to support.

```bash
research-hub setup --autonomous --vault ./vault --persona agent
research-hub auto "LLM agent benchmark reproducibility" --dry-run --no-nlm
research-hub crystal emit --cluster <existing-cluster> > /tmp/crystal-prompt.md
```

Expected:

- `setup --autonomous` reports the detected LLM CLI.
- `auto --dry-run` prints a plan and does not mutate the vault.
- If no LLM CLI is available, the workflow fails closed or emits a manual
  prompt instead of fabricating summaries.

Built-in runtime adapters currently include:

- `claude`
- `codex`
- `gemini`
- `opencode`
- `aichat`
- `cursor`
- user-configured custom adapters

## 7. R / RStudio Research Workflow Smoke

R is a project/workflow language here, not an AI host. The current
support level is:

- read and summarize R research repos through `.research/` manifests;
- include R scripts, `renv.lock`, Quarto/R Markdown, data dictionaries,
  and experiment matrices in research context compression;
- operate research-hub from shell, MCP, or REST next to an R project.

Smoke:

```bash
research-hub context init
research-hub context audit
research-hub context compress --print-prompt > /tmp/research-context.md
```

Expected:

- `.research/` files can describe R scripts, data, and experiments.
- No claim is made that research-hub is an R package or RStudio add-in.

## Verification Record

When a smoke run passes, record:

- date;
- OS;
- Python version;
- research-hub version;
- AI host and version;
- Zotero Desktop/Web API status;
- NotebookLM login method;
- commands run;
- failures and manual workarounds.
