# Stable Public API

> Status: **contract** as of v0.91.0. Codifies G2 audit #13 from the
> v0.89.1 post-release scorecard. Anything documented here follows
> semantic-versioning guarantees; anything NOT here is internal and may
> change in any release without a deprecation cycle.

research-hub has three public surfaces. Only the items listed below are
covered by the stability guarantee.

## 1. Python import surface

`research_hub.__all__` is the authoritative list:

```python
from research_hub import (
    __version__,
    # Structured exception hierarchy (since v0.89.0)
    ResearchHubError,
    MissingCredential,
    RequiresAuthRefresh,
    MissingExternalTool,
    UpstreamRateLimited,
    UpstreamUnavailable,
    # Capability manifest helpers (since v0.89.0)
    build_manifest,
    describe_manifest,
)
```

**Not public**, despite being importable:

- Any underscore module (`research_hub._deprecation`,
  `research_hub.search._rank`, …).
- Deep submodule imports (`research_hub.paper.set_labels`,
  `research_hub.dashboard.types`, …). Tests reach into these; that is a
  test-only convenience, not a contract. They may move or change
  signature in any release.
- `research_hub.cli` internals (`_emit_cli_json`, `_json_safe`,
  `_main_dispatch`). The CLI is public via its **subcommands**, not its
  Python functions.

If you need something from the internal tree as a stable API, open an
issue — we will promote it into `__all__` with a documented shape.

## 2. CLI subcommands

`research-hub describe --json` is the machine-readable source of truth
for the subcommand list. Every subcommand it reports with
`supports_json: true` emits the versioned envelope documented in
[`file-formats.md`](file-formats.md#cli---json-envelope-v0910-w5).

The subcommand **names** and their documented flags are stable. Help
text, internal flag defaults, and the per-command `report` payload
shape are NOT yet frozen (per-Report `schema_version` is tracked for
v0.92, G2 #8). Until then, branch on the envelope's `command` field.

## 3. MCP tools

The FastMCP server's tool **names** + input schemas are stable. The
full list is introspectable via `research-hub describe --json`
(`mcp_tools` key) or by starting the server. Tool consolidation
(merging overlapping `ask_cluster*` / `*_rebind` / `list_*` tools) is
tracked for v0.95-rc and will go through the deprecation cycle below.

## Deprecation policy

Implemented in `research_hub._deprecation`:

- `warn_deprecated(what, *, replacement, removed_in, stacklevel=2)`
  emits a standardized `DeprecationWarning`.
- `deprecated_callable(func, *, what, replacement, removed_in)` wraps a
  renamed callable so the old name keeps working but warns.

Rules:

1. A deprecation always emits `DeprecationWarning` naming the
   replacement and the removal version.
2. Minimum one **minor** version grace period. Removal only on a minor
   bump, never a patch.
3. Each deprecation gets a CHANGELOG entry under a `### Deprecated`
   heading (Keep-a-Changelog convention; tracked for v0.95-rc as part
   of the CHANGELOG-format rework, G4 #4).
4. The backwards-compat exception import paths
   (`research_hub.notebooklm.NotebookLMError`, etc.) noted as
   "deprecation TBD" in CHANGELOG v0.89.0 are hereby declared
   **supported aliases**, not deprecated — they re-export from
   `research_hub.errors` and inherit the new base. No removal planned.

## See also

- [`file-formats.md`](file-formats.md) — hidden file format schema versions
- v0.89.1 G2 audit: `~/.claude/plans/delegated-puzzling-umbrella.md`
- `research_hub.describe` — runtime capability manifest
