"""v0.88.12: backfill the v0.88.4 frontmatter-list dedupe across an
existing vault.

v0.88.4 made ``_render_field`` order-preserving dedupe list values on
write. But the dedupe only fires when the frontmatter is *re-written*
(via ``_rewrite_paper_frontmatter``). Notes that pre-dated v0.88.4
and were never touched since then still carry their duplicate
``cluster_queries`` / ``tags`` / ``collections`` lines on disk —
exactly the pattern W3 flagged on 10/12 human-water-llm papers.

This module walks all paper notes, looks for list-valued frontmatter
fields with duplicates, and re-writes them via the v0.88.4 plumbing.
Dry-run + apply just like the other vault migrations.

Public surface:
    migrate_all(vault_root, *, cluster_slug_filter=None, dry_run=True)
        -> list[DedupeResult]

CLI wiring lives in cli.py as ``vault cleanup-frontmatter --dedupe-lists``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Frontmatter list fields we'll dedupe. Conservative — only the fields
# we know are append-mutated by enrich/ingest flows. Won't touch fields
# whose duplicates might be intentional (currently none, but the
# allow-list keeps the blast radius small).
_DEDUPE_FIELDS = (
    "cluster_queries",
    "tags",
    "collections",
    "aliases",
)


@dataclass
class DedupeResult:
    path: Path
    action: str
    fields_deduped: list[str]
    before: dict[str, int]
    after: dict[str, int]


def migrate_one_note(note_path: Path, *, dry_run: bool) -> DedupeResult:
    """Dedupe list-valued frontmatter fields in a single note.

    Returns ``action`` ∈ {``deduped``, ``clean``, ``skipped_no_frontmatter``,
    ``skipped_no_lists``}. ``fields_deduped`` lists which fields had
    duplicates collapsed; ``before`` / ``after`` map field -> length.
    """
    from research_hub.paper import (
        _parse_frontmatter,
        _rewrite_paper_frontmatter,
        _split_frontmatter,
        _read_text_preserve_newlines,
    )

    if not note_path.exists():
        return DedupeResult(
            path=note_path, action="skipped_no_frontmatter",
            fields_deduped=[], before={}, after={},
        )
    text = _read_text_preserve_newlines(note_path)
    split = _split_frontmatter(text)
    if split is None:
        return DedupeResult(
            path=note_path, action="skipped_no_frontmatter",
            fields_deduped=[], before={}, after={},
        )

    meta = _parse_frontmatter(text)
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    updates: dict[str, object] = {}
    fields_deduped: list[str] = []
    for field in _DEDUPE_FIELDS:
        value = meta.get(field)
        if not isinstance(value, list):
            continue
        if not value:
            continue
        seen: set[str] = set()
        deduped: list[object] = []
        for item in value:
            sig = str(item)
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(item)
        before[field] = len(value)
        after[field] = len(deduped)
        if len(deduped) < len(value):
            fields_deduped.append(field)
            updates[field] = deduped

    if not fields_deduped:
        return DedupeResult(
            path=note_path,
            action="clean" if any(isinstance(meta.get(f), list) for f in _DEDUPE_FIELDS) else "skipped_no_lists",
            fields_deduped=[],
            before=before,
            after=after,
        )

    if not dry_run:
        # v0.88.4: _rewrite_paper_frontmatter routes through _render_field
        # which dedupes lists automatically — passing the unique list as
        # the update value is belt-and-suspenders + makes the report
        # explicit.
        _rewrite_paper_frontmatter(note_path, updates)

    return DedupeResult(
        path=note_path,
        action="deduped",
        fields_deduped=fields_deduped,
        before=before,
        after=after,
    )


def migrate_all(
    vault_root: Path,
    *,
    cluster_slug_filter: str | None = None,
    dry_run: bool = True,
) -> list[DedupeResult]:
    """Walk every paper note in the vault and dedupe list frontmatter
    fields. Mirrors the call shape of tag_migrate / hub_backlink_migrate
    so the CLI wiring is consistent.
    """
    raw_root = Path(vault_root) / "raw"
    if not raw_root.exists():
        return []
    results: list[DedupeResult] = []
    if cluster_slug_filter:
        cluster_dirs = [raw_root / cluster_slug_filter]
        cluster_dirs = [d for d in cluster_dirs if d.exists()]
    else:
        cluster_dirs = sorted(p for p in raw_root.iterdir() if p.is_dir() and not p.name.startswith("_"))
    for cluster_dir in cluster_dirs:
        for note_path in sorted(cluster_dir.glob("*.md")):
            results.append(migrate_one_note(note_path, dry_run=dry_run))
    return results
