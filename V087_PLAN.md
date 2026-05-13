# research-hub v0.87 plan — "trust the ingest"

> Status: **SIGNED OFF** by Wenyu 2026-05-13. Source: 4-agent audit on
> the freshly-ingested `human-water-llm` cluster (12 papers across
> Zotero + Obsidian + NLM). Agent reports archived in session;
> baseline data at `.ai/human_water_llm_audit.json` +
> `.ai/audit_human_water_llm.py`.

## Locked decisions (from open-question round, 2026-05-13)

1. **Tag scheme**: `topic:<slug>` (was inconsistent `cluster/<slug>` in
   v0.86 — new code uses `topic:` from the start; legacy
   `cluster/<slug>` tag migration deferred to v0.87.1).
2. **MOC notes** (`hub/_moc/LLM-Agents.md`, `hub/_moc/Water-Resources.md`):
   **promoted into v0.87.0 scope** (was v0.88).
3. **NLM artifact download parity**: slide-deck is the primary user
   demand. v0.87.0 ships `--type {brief, slide-deck}`; other artifacts
   (audio/video/mind-map/quiz/infographic) deferred to v0.87.1.
4. **Synthesis prompt wording** (N2): approved as drafted.
5. **🟢 30+ Zotero orphan collections**: not deleted. v0.87.0 adds a
   `kept-by-user` tag on each plus a `zotero gc --respect-kept`
   flag so future audits skip them.

### Updated scope split

| Release | New scope |
|---|---|
| **v0.87.0** | D1 doctor / N1 NLM ingest validator / Z1 PDF reporter / O1+O2 hub overview + brief mirror / O4 ingest gap reporter / N2 synthesis prompt / **MOC notes (new)** / **slide-deck download (new)** / **kept-by-user tag flow (new)** / **new code uses `topic:` (new)** |
| **v0.87.1** | Z2 abstract fallback / Z3 venue fallback / Z4 Obsidian backlink / Z5 legacy `cluster/` → `topic:` migration / O3 paper-summarize autorun / N3 remaining 5 artifacts download |

## Goal

When `research-hub ingest` returns success, the user should be able to
**trust** that:

1. Every paper that should be in the cluster IS in the cluster (no
   silent drops between fit-check and ingest).
2. Every cluster member has usable metadata (no `13-char abstract`,
   no `Vol , No` placeholder, no `Open MIND` for a Zenodo dataset).
3. Every cluster member is reachable from EVERY surface — Zotero
   item, Obsidian raw note, Obsidian hub overview, NLM source. No
   dead ends, no `.txt` orphans.
4. Failures are loud, not silent — `--with-pdfs` reports which papers
   failed and why; NLM source ingestion reports which URLs only got
   the landing-page chrome.

Today, on the `human-water-llm` cluster:
- 3 NLM sources silently failed (NLM ingested an "IEEE Xplore: Unable
  to Load Page" error page, an EGU abstract-list header, and a
  metadata-stripped ASCE record), but all 12 show `status=READY`.
- 10/12 PDFs missing despite `--with-pdfs` (only Schück + Goldshtein
  attached).
- 3 papers accepted by fit-check (`.fit_check_accepted.json` has 15
  entries) never reached `raw/` (which has 12 .md files) — IWMS-LLM,
  Making Waves, Embracing LLM. No warning.
- The NLM brief (8974 chars) is a `.txt` parked outside the vault
  tree; Obsidian can't find it.
- `hub/<cluster>/00_overview.md` is an empty zh-TW template — no
  links to the 12 papers, no NLM brief link.
- `doctor` reports `[!!] nlm_session: No saved session` even when
  login works (it checks v0.85 path layout).

## Scope split

| Release | Theme | Issues |
|---|---|---|
| **v0.87.0** | "Trust the ingest" — P0 + load-bearing P1 | D1, N1, Z1, O1+O2, O4, N2 |
| **v0.87.1** | "Clean metadata + parity" — remaining P1 + P2 | Z2, Z3, Z4, Z5, O3, N3, N4 |
| **v0.88.0** *(optional)* | "Visualization polish" — MOC notes, auto-tags, graph colors | — |

Two releases because v0.87.0 is observable user-facing behavior the
user can validate by re-running `auto`; v0.87.1 is metadata hygiene
the user verifies via the Agent A baseline JSON. Splitting reduces
risk of one bad fix poisoning the other.

## v0.87.0 — Issue catalog

For each: **ID** · **severity** · **what's broken** · **fix** ·
**files** · **acceptance test**.

### D1 · P0 · `doctor` NLM session false-positive

**What's broken**: `python -m research_hub doctor` always reports
`[!!] nlm_session: No saved session` even when login works. Checks
`<vault>/.research_hub/nlm_sessions/default/` (v0.85 directory
layout), but v0.86's notebooklm-py writes a single file at
`<vault>/.research_hub/nlm_sessions/state.json`.

**Fix**: 5-line patch in `src/research_hub/doctor.py:1180-1182`.
Replace dir check with file check:
```python
session_file = cfg.research_hub_dir / "nlm_sessions" / "state.json"
if session_file.exists() and session_file.stat().st_size > 0:
    results.append(CheckResult("nlm_session", "OK", str(session_file)))
```

**Files**: `src/research_hub/doctor.py` (1 hunk),
`tests/test_doctor.py` (update fixture that creates the legacy
`default/state.json` to create just `state.json` at the parent).

**Test**: `pytest tests/test_doctor.py::test_doctor_all_green` passes
+ smoke `python -m research_hub doctor` on a real logged-in vault
shows `[OK] nlm_session`.

**Delegation**: Claude direct (≤ 20 lines).

---

### N1 · P0 · NLM silent ingest failures (3 sources)

**What's broken**: After `upload`, NLM reports `status=READY` for
sources that actually got the wrong content:
- IEEE DOI `10.1109/iciprob...` → `"IEEE Xplore - Unable to Load Page"`
- EGU `10.5194/egusphere-egu24-15392` → `"Abstract EGU24-15392"`
  (abstract-list header only)
- ASCE `10.1061/9780784486184.086` → `"... | Vol , No"` (metadata
  stripped)

These poison NLM-generated artifacts silently — the briefing/audio
will "cover all 12 sources" without ever touching the actual content
of these 3.

**Fix**: After `wait_for_completion` in
`notebooklm/upload.py:upload_cluster`, for each Source call
`client.sources.get(id)` and check:
1. Title matches a `BAD_TITLE_PATTERNS` regex (`Unable to Load`,
   `Error \d+`, `^Abstract [A-Z]+\d+$`, `Vol\s*,\s*No`)
2. `client.sources.fulltext(id)` returns ≥ 2000 chars for non-data
   DOIs (exempt `10.5281/zenodo.*` dataset DOIs which are sparse on
   purpose)

If either check fails, emit `[WARN] source X looks like it didn't
ingest content; consider replacing the URL or uploading the PDF
manually`. Don't fail the whole upload — just surface.

**Files**: `src/research_hub/notebooklm/upload.py` (add validator
after wait loop), `tests/test_notebooklm_upload_validator.py` (new).

**Test**: monkeypatch `client.sources.get` to return titles matching
each bad pattern; assert WARN emitted, exit code 0, manifest still
written.

**Delegation**: Codex (token-heavy: needs to read notebooklm-py
Source/Notebook API surface, write validator + tests).

---

### Z1 · P0 · `--with-pdfs` silent PDF-fetch failures

**What's broken**: User passed `--with-pdfs`, expected PDFs in
Zotero attachments. Got 2/12 (only Schück + Goldshtein). Other 10
have zero attachments and zero log lines explaining why.

**Hypothesis** (to verify in fix): `pdf_attach.py` walks
arXiv/Unpaywall/OpenAlex, catches all exceptions silently, returns 0
attachments without distinguishing "paywall (403)" / "404" / "no OA
record" / "network error".

**Fix**: In `src/research_hub/zotero/pdf_attach.py`, change the
exception handler around each fetch attempt to capture `(source,
reason, http_status)` tuples per paper. At end of ingest, print a
summary table:
```
PDF attachment: 2/12 succeeded
  [OK]   schück2026  (openalex, 1.2 MB)
  [OK]   goldshtein2025  (crossref, 800 KB)
  [SKIP] arnold2026  (no OA record on Unpaywall — Zenodo dataset)
  [FAIL] taormina2024  (Unpaywall 404)
  [FAIL] wang2026     (Elsevier paywall 403)
  ...
```

**Files**: `src/research_hub/zotero/pdf_attach.py`,
`src/research_hub/pipeline.py` (call the new summary at end of ingest),
`tests/test_pdf_attach_reporting.py` (new).

**Test**: feed a synthetic 5-paper batch with mocked fetchers
returning each failure mode; assert summary lines match.

**Delegation**: Codex (touches multiple fetchers, needs tests).

---

### O1+O2 · P0 · `00_overview.md` empty + NLM brief orphan

**What's broken**: Two coupled issues that together kill the Obsidian
entry point:
- `hub/<cluster>/00_overview.md` is a 800-byte zh-TW template with
  every section empty (TL;DR, 核心問題, 必讀論文, 時間線, etc.).
- The 8974-char NLM brief is saved as a `.txt` at
  `.research_hub/artifacts/<cluster>/brief-<ts>.txt` — outside the
  vault tree, no `.md` frontmatter, not in any graph, not searchable
  from Obsidian, no link from the overview.

A user opening Obsidian and clicking into `human-water-llm` sees an
empty page and 12 disconnected paper notes. The NLM brief that took
60s to generate is invisible.

**Fix**: New module `src/research_hub/vault/hub_overview.py` with
function `populate_overview(cluster_slug, brief_path: Path | None)`:

1. Load existing `hub/<slug>/00_overview.md`; preserve any
   user-edited sections (detect via "non-placeholder" content
   heuristic).
2. Fill TL;DR from NLM brief executive summary (first 200 chars if
   brief exists, else from cluster_queries definition).
3. Add `## Papers in this cluster` section with bullet wikilinks to
   every `.md` in `raw/<slug>/` ordered by year DESC.
4. Add `## NotebookLM brief` section with a `[[notebooklm-brief-<ts>]]`
   wikilink **if the brief has been saved as .md** (next step).
5. Call from `pipeline.py` post-ingest and from `notebooklm download`
   post-save.

Plus a separate fix: when `notebooklm download --type brief` saves,
**also** write a `.md` mirror at
`hub/<slug>/notebooklm-brief-<ts>.md` with frontmatter:
```yaml
---
type: notebooklm-brief
cluster: <slug>
generated_at: <ts>
source_count: <N>
source_doi_list: [...]
nlm_notebook_url: ...
---
```
Keep the original `.txt` at `.research_hub/artifacts/...` as the
immutable archive.

**Files**:
- `src/research_hub/vault/hub_overview.py` (new, ~150 lines)
- `src/research_hub/notebooklm/download.py` (call .md-mirror helper)
- `src/research_hub/pipeline.py` (post-ingest hook)
- `tests/test_hub_overview.py` (new)
- `tests/test_notebooklm_brief_mirror.py` (new)

**Test**:
- Idempotency: running `populate_overview` twice produces identical
  output, doesn't lose user edits.
- Round-trip: a user-edited TL;DR survives a second `populate`.
- Brief mirror: `.md` exists, frontmatter parses, wikilink from
  overview resolves.

**Delegation**: Codex (largest module, ~250 LOC + 80 LOC tests).

---

### O4 · P0/P1 · fit-check → ingest silent drop

**What's broken**: `.fit_check_accepted.json` records 15 accepted
DOIs but `raw/<slug>/*.md` contains only 12. Three papers
(IWMS-LLM, Making Waves, Embracing LLM) were dropped during
ingest — likely because Semantic Scholar was rate-limited (429) and
the `add`/`ingest` retry chain gave up silently.

**Fix**: After ingest, in `pipeline.py`, compute the diff:
```
accepted = set of DOIs in .fit_check_accepted.json
ingested = set of `doi:` frontmatter in raw/<slug>/*.md
gap      = accepted - ingested
```
Write `hub/<slug>/.ingest_gap.json` with `{gap: [{doi, title,
last_error}]}` and emit one log line per gap entry naming the failure
reason (caught from the fetcher).

**Files**: `src/research_hub/pipeline.py` + new helper in
`src/research_hub/ingest_diff.py` (small).

**Test**: synthetic accepted list of 5 + raw/ with 3 → gap.json has
2 entries with reasons. Re-running ingest after S2 cool-down should
shrink the gap.

**Delegation**: Claude direct (~30 lines).

---

### N2 · P1 → P0-promoted · Brief is single-source, not synthesis

**What's broken**: NotebookLM's default "Briefing Doc" picks one
source and writes about it. For `human-water-llm`, it picked
Flood-LLM and wrote 18 paragraphs about Brisbane; 11 of 12 papers
got zero mentions. This is a NotebookLM-side limitation, but
research-hub triggers the default brief path.

**Fix**: In `src/research_hub/notebooklm/upload.py`, replace the
default-briefing trigger with `client.artifacts.generate_report(
notebook_id=..., format=ReportFormat.CUSTOM, prompt=<synthesis>
)`. The synthesis prompt:
```
Synthesize across ALL sources in this notebook. For each major
theme that recurs in multiple sources, write a section that:
- names the theme
- lists which sources contribute and what each says
- notes points of agreement and disagreement
Cover every source at least once. Do NOT default-focus on one
paper. End with "Open questions across the cluster".
```

This is promoted from P1 to P0 because it's the single biggest
quality win — the brief becomes a cluster brief, which is the whole
point of running NLM on a cluster.

**Files**: `src/research_hub/notebooklm/generate.py` (or wherever
the `--type brief` path lives — find via grep).

**Test**: live integration test gated behind `RUN_LIVE_NLM=1` (skip
in CI). Local manual: regenerate brief for `human-water-llm`,
count mentions of WaterGPT / EPANET / reservoir; expect ≥ 1 each.

**Delegation**: Claude direct (small change, but needs domain
judgment on prompt wording).

---

## Sequencing

```
Day 1
 1. Claude: D1 doctor patch + commit + push (10 min)
 2. Claude: write codex task brief for O1+O2 hub_overview + brief mirror (.ai/codex_task_hub_overview.md)
 3. Codex: O1+O2 implementation (background, ~1-2 hr)
 4. Claude: O4 ingest gap reporter + commit (30 min)
 5. Claude: N2 brief prompt change + commit (20 min)

Day 2 (after Codex round 1)
 6. Claude review Codex diff for O1+O2, fix small gaps, commit
 7. Claude: write codex task for N1 NLM validator + Z1 PDF reporter
 8. Codex: N1 + Z1 implementation
 9. Claude review, run agent A audit script as regression test, commit
10. Bump CHANGELOG, tag v0.87.0, push

Day 3
11. Re-run full pipeline on a fresh test cluster, verify all 6 P0s
    addressed, hand off to user for human validation
```

**Estimated**: 1-2 hr Claude work + 2-3 hr Codex (mostly waiting).
Each issue ships as its own commit per CLAUDE.md commit discipline
("every agent boundary is a commit boundary").

## v0.87.1 — Issue catalog (sketched, expand at start of cycle)

- **Z2** Abstract fallback chain (Crossref → OpenAlex inverted-index)
  when first source returns < 50 chars
- **Z3** Venue fallback chain (`container-title` → `event` →
  `publisher` → `proceedings-title` → `archive`); Zenodo DOI prefix
  → `itemType=dataset, venue blank`; ASCE DOI `10.1061/...` →
  never use arXiv venue
- **Z4** Zotero child-note Obsidian backlink footer
- **Z5** Tag-scheme unification (pick `topic:` or `cluster/`, change
  everywhere)
- **O3** Post-ingest `paper-summarize` autorun on the 3 placeholder
  sections (Key Findings / Methodology / Relevance) from the Zotero
  abstract
- **N3** Download parity: extend `--type` choices in
  `cli.py:nlm_download` to `{brief, audio, mind-map, video,
  slide-deck, infographic, quiz}` — direct passthroughs to
  notebooklm-py
- **N4** Drop `pdfs/` materialization + `--download-pdfs` flag (dead
  since v0.86 RPC migration)

## Delegation map

| Layer | Claude direct | Codex |
|---|---|---|
| Architectural / one-line patches | D1, O4, N2 | — |
| New module > 100 LOC | — | O1+O2 hub_overview |
| Multi-fetcher refactor + tests | — | Z1 pdf_attach reporting |
| API-surface exploration + validator | — | N1 NLM ingest validator |
| Reviewing diffs, running regression, commits | All | — |

## Acceptance: regression run

Before tagging v0.87.0:
1. Delete `human-water-llm` cluster (use `clusters delete` + Zotero
   trash the collection).
2. Run `research-hub auto "LLMs for human-water systems" --max-papers 15`.
3. Run `python .ai/audit_human_water_llm.py` (Agent A's baseline
   script).
4. Compare against `.ai/human_water_llm_audit.json` (Agent A's
   baseline JSON). Expectations:
   - PDF attached count: ≥ 8/12 (was 2/12)
   - Abstract < 50 chars: 0 (was 1: Wen 2026 — actually deferred to
     v0.87.1, so this is a v0.87.1 acceptance criterion)
   - Obsidian backlink in Zotero notes: deferred to v0.87.1
   - NLM source warning: 3 entries emitted (was 0)
   - hub/00_overview.md: non-empty, has Papers section, has NLM
     brief link
   - hub/notebooklm-brief-*.md: exists, frontmatter valid

If any acceptance criterion fails, **do not tag**.

## Risks & assumptions

- **Risk**: Semantic Scholar rate-limit still in effect when re-
  running ingest for acceptance test. **Mitigation**: skip S2 in
  fetcher chain when 429 detected, fall back to Crossref+OpenAlex.
- **Risk**: NotebookLM RPC API surface changes between
  notebooklm-py 0.4.1 and a future release. **Mitigation**: pin
  notebooklm-py to `~=0.4.1` in pyproject.
- **Assumption**: `client.sources.fulltext()` exists in
  notebooklm-py 0.4.1 — Codex should verify in N1 first; if missing,
  fallback validator uses title-only heuristics.
- **Assumption**: user wants `.md` brief mirror in `hub/<slug>/`
  rather than `raw/<slug>/`. Rationale: paper notes go in `raw/`,
  cluster-level artifacts in `hub/`. Open to flip if reviewer
  disagrees.

## Open questions — resolved (see "Locked decisions" at top)
