# research-hub v0.87.1 + v0.88 plan

> Status: **DRAFT for user review** — 2026-05-13, written immediately
> after the 4-agent post-v0.87 audit (V1 scale / V2 citation / V3
> comprehension / V4 UI) and after v0.87.0 shipped & CI green.
> Source plan (read-only audit): `~/.claude/plans/delegated-puzzling-umbrella.md`
> Audit baseline data: `.ai/human_water_llm_audit.json` +
> `.ai/audit_human_water_llm.py` (Agent A's reusable script).
>
> Two releases bundled in one plan because the audit findings split
> cleanly into "metadata-hygiene quick wins" (v0.87.1) and "scale +
> UX work" (v0.88), with no cross-coupling. Same v0.87 cycle,
> separate ship moments.

## Context: what we now know from the audit

After v0.87.0 shipped 8 issues, 4 read-only agents audited the
live cluster against 4 dimensions the original 4-agent round did
not cover:

- **V1 (scale)**: research-hub stays **correct** at 1000 papers but
  becomes **unusable** past ~50/cluster. NLM 51-paper silent truncate,
  O(N) overview render, dedup-index 20 KB/paper monotonic growth.
- **V2 (citation)**: 35/35 papers are real, no hallucinated DOIs.
  But **2 papers would be rejected by a manuscript reviewer**
  (goldshtein2025 `journal=arXiv` should be ASCE; arnold2026 Zenodo
  dataset wrongly labeled "Open MIND" journal). 6 more have blank
  journal fields. The v0.87 audit flagged these — they weren't fixed.
- **V3 (comprehension)**: **51% of paper notes (18/35)** are stuck
  at score-3 with placeholder bodies (`[review and extract from
  Abstract section above]` everywhere). The older
  `llm-agents-social-interaction` cluster (23 papers) clearly got
  `paper-summarize` retroactively; today's `human-water-llm`
  (12 papers) did not. The placeholder is **sticky and silent** —
  looks structurally OK, never gets fixed.
- **V4 (UI)**: vault is functional but UX-rough. MOC stubs are dead
  ends. `llm-agents-social-interaction/00_overview.md` never got
  `populate_overview` re-run after v0.87 shipped. Paper notes
  don't backlink to their cluster overview or MOC — graph view
  shows orphan hubs. No `_HOME.md` start-here. 35 papers have
  `tags: []` empty so graph color grouping is dead.

## Locked decisions (carried from V087_PLAN.md)

1. Tag scheme: `topic:<slug>` (legacy `cluster/<slug>` migration ships now in v0.87.1)
2. Slide-deck: shipped in v0.87.0; audio/video/mind-map deferred until user demand
3. Synthesis prompt for NLM brief: locked in v0.87.0
4. Kept-by-user Zotero registry: live with 94 entries; `--list` improvements queued for v0.88

## Release split

| Release | Theme | Duration | Issues |
|---|---|---|---|
| **v0.87.1** | "trust the metadata + see the warnings" | 1-2 days | 7 issues, metadata + placeholder loop fix |
| **v0.88** | "scale + UX" | 2-3 days | 11 issues, scale cliff + vault navigation |

Two reasons to split: (a) v0.87.1 fixes are pre-condition for
re-running ingest cleanly (need venue/abstract fallback before next
cluster), v0.88 is "make the existing cluster better navigable" and
can ship independently. (b) v0.87.1 is mostly Claude-direct, v0.88
is mostly Codex; mixing them in one tag dilutes the commit log.

---

## v0.87.1 scope (7 issues)

### #1 — DOI-prefix venue overrides (Z3 hot-spots)

**Problem (V2 P0)**: 2 papers would be rejected by a reviewer:
- `goldshtein2025` — DOI `10.1061/9780784486184.086` is ASCE; venue says `arXiv`
- `arnold2026` — DOI `10.5281/zenodo.18444869` is a Zenodo dataset; venue says `Open MIND`

**Fix**: In `src/research_hub/zotero/enrich.py`, add a `DOI_PREFIX_OVERRIDES` table that runs BEFORE accepting Crossref/Semantic Scholar venue assignment:

```python
DOI_PREFIX_OVERRIDES = {
    "10.1061/":       {"forbid_venue": {"arxiv"}, "venue_fallback": ["publisher", "proceedings-title", "event-title"]},
    "10.5281/zenodo.": {"item_type": "dataset", "venue": ""},
    "10.6084/m9.figshare.": {"item_type": "dataset", "venue": ""},
    "10.22541/essoar.": {"venue_fallback": ["publisher", "archive"]},   # ESS Open Archive
    "10.31223/":      {"venue_fallback": ["publisher", "archive"]},     # EarthArXiv
    "10.5194/egusphere-": {"venue_fallback": ["event-title", "publisher"], "default_venue": "EGU General Assembly"},
}
```

**Files**: `src/research_hub/zotero/enrich.py`, `src/research_hub/zotero/fetch.py` (where itemType is decided), `tests/test_v0871_doi_prefix_overrides.py` (new).

**Acceptance**: Re-ingest goldshtein + arnold (with `--allow-library-duplicates` or via direct fetch.py call) → venue is correct + itemType=dataset for arnold. 8 new unit tests covering each prefix.

**Effort**: ~1.5h. **Delegation**: Claude direct.

### #2 — Venue fallback chain (Z3 general)

**Problem (V2 P1)**: 6 papers have blank journal — höhn / kim / qiao-thematic / fu / ranaweera / taormina (all conference proceedings or preprint platforms where Crossref puts the venue under `event-title` or `publisher` instead of `container-title`).

**Fix**: In `enrich.py` venue-assignment path, add fallback chain:
```python
def resolve_venue(crossref_record) -> str:
    for key in ("container-title", "event-title", "proceedings-title", "publisher", "archive"):
        value = crossref_record.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return value.strip()
    return ""
```

Apply DOI_PREFIX_OVERRIDES from #1 FIRST; this fallback is the
default path for everything else.

**Files**: `src/research_hub/zotero/enrich.py`, tests.

**Acceptance**: Re-enrich the 6 papers → all get non-blank venue. New unit tests with synthetic Crossref records covering each fallback layer.

**Effort**: ~2h. **Delegation**: Claude direct.

### #3 — Abstract fallback chain (Z2)

**Problem (V2 P1 + V3 P0)**: 7 papers have `(no abstract)` placeholder. The current `enrich.py` accepts whatever Crossref returns; if Crossref's abstract is empty (or <50 chars), there's no fallback. Wen 2026 is the canonical case.

**Fix**: When Crossref `abstract` is missing OR < 200 chars, try OpenAlex `abstract_inverted_index` reconstruction:
```python
def reconstruct_openalex_abstract(doi: str) -> str | None:
    r = openalex.get(f"https://api.openalex.org/works/doi:{doi}")
    inverted = r.json().get("abstract_inverted_index")
    if not inverted: return None
    word_positions = sorted(((pos, word) for word, positions in inverted.items() for pos in positions))
    return " ".join(word for _, word in word_positions)
```

**Files**: `src/research_hub/zotero/enrich.py`, tests.

**Acceptance**: Wen 2026 abstract becomes ≥ 500 chars. 5 of the 7 blank-abstract papers get OpenAlex abstracts (some won't have one — that's fine, mark `abstract_status: missing`).

**Effort**: ~2.5h. **Delegation**: Claude direct.

### #4 — `summarize_status` frontmatter + paper-summarize autorun (O3) ⭐

**Problem (V3 P0)**: 18/35 paper notes are stuck at score-3 with
sticky placeholder bodies. The current scaffold strings
(`[review and extract from Abstract section above]`,
`[review abstract; refine after reading PDF]`,
`[TODO: fill relevance to cluster]`) look structurally complete
to Dataview/Bases queries and to users — but the bodies are
useless. **This is the single highest-impact fix in either release.**

**Fix**: 4-part change:

1. **frontmatter status field**: every paper note gets
   `summarize_status: pending | done | failed_no_abstract` at ingest.
   Replaces the opaque `[TODO]` text in body sections.

2. **Body template gating**: when `summarize_status: pending`, body
   sections (`Key Findings` / `Methodology` / `Relevance`) contain
   a single explicit callout `> [!warning] Summary pending — run
   paper-summarize` rather than 3 sticky placeholders.

3. **Async paper-summarize queue worker**: new CLI
   `research-hub paper summarize --pending` walks all papers with
   `summarize_status: pending`, calls Claude/LLM via existing
   `paper-summarize` skill, fills sections, flips status to `done`.
   Idempotent: re-running on `done` skips.

4. **Dashboard surfaces backlog**: dashboard `library` tab shows
   "Papers awaiting summary: N" with a one-click run command.

**Prompt template** (Agent V3's exact spec):
```
You are summarizing an academic paper for an Obsidian vault entry.
Use ONLY the abstract below — do not invent results.

Abstract: {{abstract}}
Title: {{title}}
Cluster: {{topic_cluster}}

Output exactly four sections in markdown:
1. SUMMARY: 1-2 sentences using the paper's own terminology.
2. KEY_FINDINGS: 3-5 bullets, each starting with a concrete claim.
3. METHODOLOGY: 1 paragraph (study type, dataset, sample, primary metric).
4. RELEVANCE: 1 sentence connecting to "{{topic_cluster}}".

If abstract is <100 chars or says "(no abstract)", output
`[no-abstract-fallback]` for all four sections. Do NOT hallucinate
from the title alone.
```

**Files**: `src/research_hub/zotero/fetch.py` (frontmatter +
template), `src/research_hub/paper_summarize.py` (new queue
worker), `src/research_hub/cli.py` (new `paper summarize --pending`
subcommand), `src/research_hub/dashboard/sections.py` (backlog
metric), tests.

**Acceptance**: Run `research-hub paper summarize --pending` on the
12 human-water papers → 11 get filled bodies with substantive
content, Wen 2026 flips to `failed_no_abstract`. Score-distribution
shifts from `{1:1, 3:11}` → `{4-5:11, failed:1}`.

**Effort**: ~4h. **Delegation**: Codex (token-heavy, new queue +
CLI + dashboard tab).

### #5 — Re-run `populate_overview` for `llm-agents-social-interaction`

**Problem (V4 P0)**: Today's `populate_overview` only fired on the
human-water-llm cluster (the live smoke target). The older 23-paper
cluster overview is still bare Mandarin scaffold.

**Fix**: One-shot Claude script: walk every cluster in
`<vault>/.research_hub/clusters.json`, call `populate_overview` and
`ensure_moc` on each. Also: register this as a post-ingest hook for
every cluster, not just the active one (the v0.87 hook only fired
for the just-ingested cluster).

**Files**: `src/research_hub/vault/hub_overview.py` (add
`populate_all_overviews(cfg)`), `src/research_hub/cli.py` (new
`hub rebuild-overviews` subcommand), `src/research_hub/pipeline.py`
(post-ingest hook fix).

**Acceptance**: `llm-agents-social-interaction/00_overview.md`
gets Papers + NLM brief (if exists) + Related MOCs sections, all
12+23 = 35 papers visible from their respective overviews.

**Effort**: ~1h. **Delegation**: Claude direct.

### #6 — `topic:<slug>` tag migration (Z5)

**Problem (V4 P0)**: 35 papers have `tags: []` empty. Only
frontmatter property `topic_cluster:` is set. Obsidian tag pane is
empty for these notes, graph color grouping by topic is dead.

**Fix**: 2-part change:
1. **Migrate existing**: script that walks all paper notes, reads
   `topic_cluster:`, adds `tags: ["topic:<slug>"]` to frontmatter
   (preserving any user-added tags).
2. **Update ingest path**: in `zotero/fetch.py:make_raw_md`, write
   `tags: ["topic:<slug>"]` from `topic_cluster` at note creation.

**Files**: `src/research_hub/zotero/fetch.py`,
`src/research_hub/vault/tag_migrate.py` (new), tests.

**Acceptance**: All 35 paper notes have `tags: ["topic:<slug>"]`.
Obsidian tag pane shows `topic:human-water-llm` (12) and
`topic:llm-agents-social-interaction` (23).

**Effort**: ~1.5h. **Delegation**: Claude direct.

### #7 — Pre-flight `enrich --recheck` for 2 reviewer-rejection papers

**Problem**: Issues #1+#2+#3 fix the pipeline going forward, but the
2 papers already in Zotero/Obsidian have wrong venues. They need a
forced re-enrich.

**Fix**: New CLI command `research-hub paper enrich --slug <slug>
--force` that ignores the dedup cache and re-fetches metadata from
Crossref+OpenAlex, then writes back to Zotero + the Obsidian note's
frontmatter.

**Files**: `src/research_hub/cli.py` (subcommand), an existing
helper in `paper.py` that probably has 80% of this already.

**Acceptance**: After `research-hub paper enrich --slug
goldshtein2025-large-language-models-water --force`, the Zotero
item venue field is "Proceedings of the World Environmental and
Water Resources Congress 2025" (or close). Same for arnold2026 →
`itemType: dataset`.

**Effort**: ~1.5h. **Delegation**: Claude direct.

---

## v0.88 scope (11 issues)

### #1 — NotebookLM source-cap awareness ⭐ (V1 P0)

**Problem**: At 51 papers `upload_cluster(rate_limit_cap=50)`
silently breaks the loop. No warning, no `UploadReport.skipped`
field, no recommendation.

**Fix**: 3-part:
1. **Surface the cap**: change silent break to log + populate new
   `UploadReport.over_cap: list[Entry]` field.
2. **Hard-fail by default; opt-in to truncate**: new flag
   `--over-cap-strategy {fail | top-n-recent | top-n-cited | fit-score | shard}`.
3. **Sharding model**: extend `Cluster` dataclass with
   `notebooklm_shards: list[NotebookShard]` where each shard is
   `{notebook_id, source_range_dois}`. New CLI `notebooklm shard
   --cluster X --strategy {recent|cited|fit}` to materialize.

**Files**: `src/research_hub/notebooklm/upload.py:421,497`,
`src/research_hub/clusters.py` (Cluster dataclass),
`src/research_hub/cli.py` (shard subcommand), tests.

**Acceptance**: Synthetic test with 60-paper cluster → default
fails with clear message; `--over-cap-strategy top-n-recent`
uploads 50 newest. Cluster.notebooklm_shards round-trips.

**Effort**: ~5h. **Delegation**: Codex.

### #2 — Paginated overview + debounced rebuild (V1 P0)

**Problem**: `populate_overview` enumerates ALL papers as wikilinks.
At 200+ papers the overview is a wall the user scrolls past.
Plus: it runs on every single-paper ingest → O(N²) per session.

**Fix**:
1. **Pagination**: when `len(papers) > 30`, render only top-12
   "Recent" + top-20 "Most-cited (or by fit score)" inline; rest
   goes into a sidecar `01_papers_by_year.md` linked from the
   overview.
2. **Debouncing**: skip rebuild if fewer than 10 papers were added
   since last `.last_rebuild` timestamp. Force rebuild via
   `hub rebuild-overviews --force`.

**Files**: `src/research_hub/vault/hub_overview.py`, tests.

**Acceptance**: Build a synthetic 200-paper cluster → overview
shows 32 papers inline + 168 in sidecar. Second ingest of 5 more
papers → overview unchanged (debounced). Force rebuild → updated.

**Effort**: ~4h. **Delegation**: Codex.

### #3 — `paper bulk-*` + `clusters archive` + `dedup compact` (V1 P2)

**Problem**: At scale the user needs bulk operations. Today only
single-slug `prune --label`, `unarchive` exist.

**Fix**:
- `paper bulk-relabel --from X --to Y --cluster Z [--by-doi-prefix P]`
- `paper bulk-move --slugs A,B,C --to-cluster X`
- `paper bulk-delete --by-tag X [--dry-run]` (Zotero + Obsidian)
- `clusters archive <slug>` — sets `status: archived` on Cluster,
  excludes from default ingests + overviews, keeps notes searchable.
  Reversible via `clusters unarchive`.
- `dedup compact` — drops stale Zotero hits from dedup_index
  (mirror of existing `rebuild_from_obsidian`).

**Files**: `src/research_hub/cli.py`, `src/research_hub/paper.py`,
`src/research_hub/clusters.py` (status field), `src/research_hub/dedup.py`,
tests.

**Acceptance**: bulk-relabel 5 papers in a synthetic cluster
working. clusters archive → cluster excluded from `auto` runs.
dedup compact removes 0 entries on a healthy vault, removes N on a
seeded stale-vault fixture.

**Effort**: ~5h. **Delegation**: Codex.

### #4 — MOC body populator (V4 P0)

**Problem**: `ensure_moc` only creates the file; never updates the
body. `## Clusters tagged with this MOC` is permanently `(populated
by sync)`.

**Fix**: New `populate_moc(name, cluster_slugs)` function in
`vault/hub_overview.py`. Walks every cluster.json entry, finds
clusters whose `moc_links` includes this MOC name, writes
`## Clusters\n- [[<cluster>/00_overview]] (<slug>)` list. Called
from `populate_overview` after every cluster sync.

**Files**: `src/research_hub/vault/hub_overview.py`, tests.

**Acceptance**: `hub/_moc/LLM-Agents.md` body contains
`[[human-water-llm/00_overview]]` and (if applicable)
`[[llm-agents-social-interaction/00_overview]]`. Idempotent on
re-run.

**Effort**: ~2h. **Delegation**: Claude direct.

### #5 — Paper note `## Hub` section (V4 P0)

**Problem**: Papers don't backlink to their cluster overview or
MOC. Graph shows orphan hubs.

**Fix**: In `zotero/fetch.py:make_raw_md`, append a `## Hub`
section at the bottom of every new paper note:
```markdown
## Hub
- Cluster: [[<cluster>/00_overview]]
- MOC: [[<moc>]] (one per MOC link)
```

Also: migration script for the existing 35 papers.

**Files**: `src/research_hub/zotero/fetch.py`,
`src/research_hub/vault/hub_backlink_migrate.py` (new), tests.

**Acceptance**: Every paper note has `## Hub` section with at
least the cluster wikilink. Migration script idempotent.

**Effort**: ~2h. **Delegation**: Claude direct.

### #6 — Brief mirror TL;DR + cluster backlink (V4 P0, mobile)

**Problem**: NLM brief mirror opens straight into long synthesis —
iPhone scrolling pain. Plus no `[[00_overview]]` backlink in body.

**Fix**: In `notebooklm/download.py:mirror_brief_and_populate_overview`,
prepend before the synthesis body:
```markdown
## TL;DR
(first 3-5 lines extracted from NLM brief's "Executive Summary" / 
"Key Themes" section, capped 500 chars)

**Cluster:** [[<cluster>/00_overview]]
```

**Files**: `src/research_hub/notebooklm/download.py`, tests.

**Acceptance**: New brief mirror starts with TL;DR + cluster
backlink. Existing brief mirror gets re-generated on next download.

**Effort**: ~1.5h. **Delegation**: Claude direct.

### #7 — Vault root `_HOME.md` (V4 P0, start-here)

**Problem**: No canonical landing page. Dashboard is HTML outside
Obsidian, MOCs are stubs, no pinned start file.

**Fix**: New `populate_home(vault_root)` in
`vault/hub_overview.py`. Generates `<vault>/_HOME.md`:
```markdown
---
type: home
aliases: ["Home", "🏠"]
---
# Research Hub

## Clusters
- [[human-water-llm/00_overview|LLM for Human-Water Systems]] (12 papers) — [[LLM-Agents]] / [[Water-Resources]]
- [[llm-agents-social-interaction/00_overview|LLM Agents in Social Interaction]] (23 papers) — [[LLM-Agents]]

## Reading queue
(top 5 papers with status: unread, year DESC)

## Recent NotebookLM briefs
(latest 3 brief mirrors)

## Dashboard
- [Dashboard HTML](.research_hub/dashboard.html) (desktop only)
```

**Files**: `src/research_hub/vault/hub_overview.py`,
`src/research_hub/pipeline.py` (post-ingest hook calls populate_home), tests.

**Acceptance**: `<vault>/_HOME.md` exists, lists both clusters,
shows reading queue, regenerates idempotently.

**Effort**: ~2.5h. **Delegation**: Claude direct.

### #8 — Auto-section visual fence (V4 P1)

**Problem**: Generated sections in overview interleave with
hand-fill sections. No visual cue. Risk: user edits auto-section,
loses edit on next `populate_overview`.

**Fix**: In `vault/hub_overview.py:_render_overview`, wrap
auto-generated sections with HTML markers + Obsidian callout:
```markdown
%% AUTO-BEGIN %%
> [!info] Auto-generated by populate_overview
> Edits to this section will be overwritten. Add hand-written
> content under "核心問題" or "必讀論文" below.

## Papers in this cluster
- ...
%% AUTO-END %%
```

Preservation logic: skip anything between AUTO-BEGIN/AUTO-END
markers when computing user-preserved sections.

**Files**: `src/research_hub/vault/hub_overview.py`, tests.

**Effort**: ~1.5h. **Delegation**: Claude direct.

### #9 — `status==unread` base view (V4 P1)

**Problem**: Every paper note ships with `status: unread` but no
base view filters on it. The base's first tab is "Papers by year"
— useful but generic.

**Fix**: Add a "Reading queue" view to the generated base file
template: `filter: status == "unread"`, `order: year DESC, ingested_at DESC`.

**Files**: `src/research_hub/obsidian_bases.py` (or wherever the
base template lives).

**Effort**: ~30min. **Delegation**: Claude direct.

### #10 — `mark-kept --list` enhancements (V1 P1)

**Problem**: 94 opaque 8-char Zotero keys is unreadable. At 500+
it's useless.

**Fix**: Extend `cli.py:_zotero_mark_kept` to support:
- `--by-name` — look up collection name from Zotero (paginated)
- `--show-counts` — print `<key>  <name>  <num_items>`
- `--by-pattern PAT` — filter kept keys by collection-name regex

**Files**: `src/research_hub/cli.py`, possibly
`src/research_hub/zotero/gc.py` (add `lookup_names`), tests.

**Effort**: ~2h. **Delegation**: Claude direct.

### #11 — Dashboard reachable from Obsidian (V4 P1)

**Problem**: Dashboard is `.research_hub/dashboard.html` — Obsidian
mobile/desktop user can't open it from inside the vault.

**Fix**: `_HOME.md` (issue #7) already includes the link, but
file:// links don't work on iOS. Two-part fix:
- Continue file:// for desktop.
- For mobile: add a `dashboard-summary.md` generated by
  `research-hub dashboard --markdown-summary` that mirrors the
  top-line metrics (paper count, brief count, ingest backlog,
  doctor status) into a markdown file readable in Obsidian.

**Files**: `src/research_hub/dashboard/render.py` (new
markdown-summary writer), tests.

**Effort**: ~2h. **Delegation**: Claude direct.

---

## Sequencing

```
Day 1 (Claude direct, fast wins)
  Morning:
    v0.87.1 #5  re-run populate_overview on llm-agents-social         30min
    v0.87.1 #6  topic: tag migration                                  1.5h
    v0.87.1 #1  DOI-prefix venue overrides                            1.5h
  Afternoon:
    v0.87.1 #2  venue fallback chain                                  2h
    v0.87.1 #3  abstract fallback chain                               2.5h
    v0.87.1 #7  enrich --recheck for goldshtein + arnold              1.5h
    → commit + push v0.87.1 partial (no #4 yet)

Day 2 (Codex round 1, big one)
  Codex task brief: v0.87.1 #4 (summarize_status + autorun + queue + dashboard tab)
  Dispatch Codex (background ~1h)
  Meanwhile Claude does v0.88 #4 (MOC body populator)
  Meanwhile Claude does v0.88 #5 (paper note ## Hub section)
  Codex returns → Claude reviews diff → commit
  → bump CHANGELOG + tag v0.87.1, push

Day 3 (Codex round 2 + Claude UX work)
  Codex task brief: v0.88 #1 (NLM source-cap) + #2 (paginated overview) + #3 (bulk ops)
  Dispatch Codex (background ~2h)
  Meanwhile Claude does v0.88 #6 (brief TL;DR), #7 (_HOME.md), #8 (visual fence)

Day 4 (finishing)
  Codex returns v0.88 #1-3 → review + commit
  Claude does v0.88 #9 (unread view), #10 (mark-kept), #11 (dashboard md summary)
  Run full audit again (Agent A's script + a fresh V1-V4 round)
  → bump CHANGELOG + tag v0.88, push
  → wait for CI green
```

Estimated total: 14-16 hours of Claude work + 3-4 hours of Codex
runtime (mostly waiting). Realistically 3 calendar days if dispatched
across worktrees.

## Delegation map

| Layer | Claude direct | Codex |
|---|---|---|
| Architectural decisions, 5-line patches, plumbing | v0.87.1 #1, #2, #3, #5, #6, #7; v0.88 #4, #5, #6, #7, #8, #9, #10, #11 | — |
| Token-heavy new modules + tests | — | v0.87.1 #4, v0.88 #1, #2, #3 |
| Reviews, regression, commits | all | — |

Total: 14 Claude-direct tickets + 4 Codex tickets.

## Acceptance / regression plan

Before tagging EITHER release:
1. Re-run Agent A's audit script `python .ai/audit_human_water_llm.py` against `human-water-llm` cluster.
2. Compare key metrics:
   - **goldshtein2025 venue**: must NOT be "arXiv"
   - **arnold2026 itemType**: must be "dataset"
   - **wen2026 abstract**: must be > 200 chars (or `summarize_status: failed_no_abstract` after OpenAlex retry)
   - **abstract<200 count**: 7 → 0 or 1 (only Wen if OpenAlex also fails)
   - **placeholder Methodology count**: 18 → 0 or 1
   - **score-3 count**: 12 → 0 or 1
   - **score-5 count**: 16 → 28+ (after autorun)
   - **All paper notes have `tags: ["topic:<slug>"]`**: all 35
   - **All paper notes have `## Hub` section**: all 35 (v0.88 only)
   - **`hub/_moc/LLM-Agents.md` body has `## Clusters` list**: yes (v0.88 only)
   - **`<vault>/_HOME.md` exists**: yes (v0.88 only)
3. Run `python -m pytest -q -m "not slow"` — must not regress from 2113 baseline (expect ~2150 after all new tests).
4. Run `research-hub doctor` — all green.
5. Live smoke: `research-hub notebooklm download --cluster human-water-llm --type brief` — new brief mirror has TL;DR + backlink (v0.88).
6. NLM cap test (synthetic, v0.88): seed 60-paper cluster → upload defaults to fail with clear message.

Each metric mapped to acceptance check in the audit script; failure
on any → do not tag.

## Risks

- **OpenAlex rate-limit on abstract fetch**: 100k requests/day for
  polite pool. 7 abstract fetches today = nothing, but a 1000-paper
  re-enrich could hit it. Mitigation: cache abstracts in
  `enrich_cache.json`; retry with `Retry-After`.
- **Existing paper notes already have `[TODO]` placeholders**:
  v0.87.1 #4 changes the body template. Existing notes must be
  migrated, not just new ones. Migration script needs to detect
  placeholder text and overwrite with the new `> [!warning]` callout
  + flip `summarize_status: pending`.
- **NLM sharding model adds complexity**: v0.88 #1 introduces a list
  of notebooks per cluster. Downstream code that assumes 1:1 cluster→
  notebook (e.g. `notebooklm bundle`, `notebooklm download`) needs
  audit. Define this in the Codex brief.
- **`paper bulk-delete`** can wipe data. Default `--dry-run` MUST be
  ON; require `--apply` to actually delete. Mirror the existing
  `zotero gc` safety pattern.

## Open questions for the user before kickoff

1. **For v0.87.1 #4 (summarize_status + autorun)**: which LLM
   does the autorun call? Claude (via Anthropic API direct), Codex
   CLI, or `paper-summarize` skill via Claude Code? Recommendation:
   start with `paper-summarize` skill since it's already wired and
   we don't need raw API.
2. **For v0.88 #1 (NLM sharding)**: at 51+ papers, default behavior
   should be (a) hard-fail with recommendation, (b) auto-shard,
   (c) auto-prune to top-50 by year. Recommendation: (a) — explicit
   user intent required.
3. **For v0.88 #7 (`_HOME.md`)**: name + position. Default
   `_HOME.md` at vault root, pinned via Obsidian's Starred plugin.
   Alternative: `00_HOME.md` so it sorts first in file explorer.
4. **Want v0.87.1 + v0.88 in same git branch + one CI run, or two
   separate ship cycles?** Recommendation: ship v0.87.1 standalone
   first (it's a quick win + de-risks subsequent v0.88 work).

---

*Once you sign off (or amend the open questions), I'll start with
v0.87.1 #5 (the 30-minute populate_overview re-run, smallest fix)
and work the sequence as written.*
