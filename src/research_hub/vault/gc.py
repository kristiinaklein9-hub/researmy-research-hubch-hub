"""Vault garbage collection (v1.1 P2-5d).

Four maintenance passes, all DRY-RUN by default (the caller passes
``apply=True`` to execute):

1. ``purge_aged_deleted`` — hard-remove ``raw/_deleted_<slug>/`` soft-delete
   residue older than a threshold. These dirs are ALREADY soft-deleted (moved
   there by ``cascade_delete_cluster``); purging them touches no live note and
   no Zotero item — it only reclaims disk and stops the residue from polluting
   footer-prune / graph passes (the bug that blocked the real-vault migration).
2. ``find_orphan_hubs`` — ``hub/<slug>/`` dirs with NO registry entry at all
   (``include_merged=True`` so a tombstoned-but-present cluster is never an
   orphan; only hubs whose cluster is entirely gone qualify).
3. ``find_orphan_mocs`` — ``hub/_moc/<name>.md`` pages that no live cluster
   references. ``PARENT_MOCS`` are ALWAYS protected (the v1.0.7 GC invariant).
4. ``strip_hub_parents`` — drop the bare ``- MOC: [[LLM-Agents]]`` /
   ``[[Water-Resources]]`` lines from existing paper-note ``## Hub`` blocks so
   the live graph matches the P1-4a ingest policy (paper notes link the
   sub-MOC only). This is the rewrite the real-vault graph migration needs.

Nothing here mutates a cluster's Zotero binding. Soft-deleted residue is the
only thing hard-removed, and only when aged past the threshold.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from research_hub.vault.hub_overview import PARENT_MOCS, derive_moc_links

_DELETED_PREFIX = "_deleted_"
# Reserved hub subdirs that are never cluster-slug hubs.
_RESERVED_HUB_DIRS = {"_moc", "_archived"}
# A bare-parent MOC line inside a `## Hub` block, e.g. `- MOC: [[LLM-Agents]]`.
# The end-of-line anchor (``(?:\r?\n|$)``) ensures we only strip a line that is
# EXACTLY the bare-parent MOC bullet — a line with trailing text after the ``]]``
# (never machine-written, but defensive) is left untouched rather than half-cut.
_BARE_PARENT_MOC_RE = re.compile(
    r"^[ \t]*-[ \t]*MOC:[ \t]*\[\[(?:"
    + "|".join(re.escape(p) for p in PARENT_MOCS)
    + r")\]\][ \t]*(?:\r?\n|$)",
    re.MULTILINE,
)


def _under_deleted_dir(path: Path) -> bool:
    """True if *path* sits inside ``raw/_deleted_<slug>/`` residue."""
    return any(part.startswith(_DELETED_PREFIX) for part in path.parts)


@dataclass
class GcReport:
    """What a vault-gc run did (or would do, when ``applied`` is False)."""

    applied: bool = False
    older_than_days: int = 30
    aged_deleted: list[dict[str, object]] = field(default_factory=list)
    orphan_hubs: list[str] = field(default_factory=list)
    orphan_mocs: list[str] = field(default_factory=list)
    hub_parents_stripped: list[str] = field(default_factory=list)

    def total_actions(self) -> int:
        return (
            len(self.aged_deleted)
            + len(self.orphan_hubs)
            + len(self.orphan_mocs)
            + len(self.hub_parents_stripped)
        )


def _dir_age_days(path: Path, now: datetime) -> float:
    """Age of *path* in days from its mtime (UTC)."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (now - mtime).total_seconds() / 86400.0


def purge_aged_deleted(
    raw_dir: Path,
    *,
    older_than_days: int = 30,
    apply: bool = False,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Hard-remove ``raw/_deleted_<slug>/`` residue older than the threshold.

    Returns one record per purged (or would-purge) dir. ``now`` is injectable
    for deterministic age tests.
    """
    now = now or datetime.now(timezone.utc)
    if not raw_dir.exists():
        return []
    actions: list[dict[str, object]] = []
    for child in sorted(raw_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith(_DELETED_PREFIX):
            continue
        age = _dir_age_days(child, now)
        if age < older_than_days:
            continue
        actions.append(
            {"path": str(child), "name": child.name, "age_days": round(age, 1)}
        )
        if apply:
            shutil.rmtree(child)
    return actions


def find_orphan_hubs(hub_dir: Path, live_slugs: set[str]) -> list[Path]:
    """``hub/<slug>/`` dirs whose slug has no registry entry at all.

    Reserved dirs (``_moc``, ``_archived``) are never orphans.
    """
    if not hub_dir.exists():
        return []
    orphans: list[Path] = []
    for child in sorted(hub_dir.iterdir()):
        if not child.is_dir() or child.name in _RESERVED_HUB_DIRS:
            continue
        if child.name not in live_slugs:
            orphans.append(child)
    return orphans


def find_orphan_mocs(hub_dir: Path, referenced_mocs: set[str]) -> list[Path]:
    """``hub/_moc/<name>.md`` pages no live cluster references.

    ``PARENT_MOCS`` are ALWAYS treated as referenced — a single family with no
    current cluster must still keep its parent hub (v1.0.7 GC invariant).
    """
    moc_dir = hub_dir / "_moc"
    if not moc_dir.exists():
        return []
    protected = set(referenced_mocs) | set(PARENT_MOCS)
    orphans: list[Path] = []
    for md_path in sorted(moc_dir.glob("*.md")):
        if md_path.stem not in protected:
            orphans.append(md_path)
    return orphans


def referenced_mocs_for(registry) -> set[str]:
    """Union of MOC names every live (incl. merged-but-present) cluster links.

    CRITICAL: derive names WITH ``cluster_queries`` (not just the slug). MOC
    pages are created during ingest from BOTH the slug and the first query
    (hub_overview.populate_all_mocs), so a cluster whose slug lacks a family
    keyword but whose QUERY carries one still owns a query-derived sub-MOC. The
    merge-time GC guard (clusters._gc... ``links_for``) passes the query for the
    same reason — omitting it here would orphan-and-delete a LIVE sub-MOC that
    ``populate_all_mocs`` never regenerates (the dangling-link over-deletion bug
    documented at clusters.py).
    """
    refs: set[str] = set(PARENT_MOCS)
    for cluster in registry.list(include_merged=True):
        slug = (cluster.slug or "").strip()
        if not slug:
            continue
        explicit = list(getattr(cluster, "moc_links", []) or [])
        queries = [str(getattr(cluster, "first_query", "") or "")]
        refs.update(derive_moc_links(slug, cluster_queries=queries, moc_links=explicit))
    return refs


def strip_hub_parents(
    vault_raw_dir: Path,
    *,
    apply: bool = False,
) -> list[str]:
    """Drop bare ``- MOC: [[LLM-Agents]]`` / ``[[Water-Resources]]`` lines from
    existing paper-note ``## Hub`` blocks (P1-4a back-correction).

    Sub-MOC lines (``[[LLM-Agents-Human]]``) are kept. Soft-deleted residue is
    skipped. Returns the relative paths of notes that changed.
    """
    if not vault_raw_dir.exists():
        return []
    changed: list[str] = []
    for md_path in sorted(vault_raw_dir.rglob("*.md")):
        if _under_deleted_dir(md_path):
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "## Hub" not in text:
            continue
        new_text = _BARE_PARENT_MOC_RE.sub("", text)
        if new_text != text:
            changed.append(str(md_path.relative_to(vault_raw_dir)))
            if apply:
                md_path.write_text(new_text, encoding="utf-8")
    return changed


def run_gc(
    cfg,
    *,
    older_than_days: int = 30,
    apply: bool = False,
    strip_parents: bool = True,
    now: datetime | None = None,
) -> GcReport:
    """Run all gc passes against the vault described by *cfg*.

    ``cfg`` must expose ``raw``, ``hub`` and ``clusters_file``. Dry-run unless
    ``apply=True``. ``strip_parents=False`` skips the paper-note Hub-block
    rewrite (the only content-mutating pass).
    """
    from research_hub.clusters import ClusterRegistry

    raw_dir = Path(cfg.raw)
    hub_dir = Path(cfg.hub)
    registry = ClusterRegistry(cfg.clusters_file)
    live_slugs = {
        (c.slug or "").strip()
        for c in registry.list(include_merged=True)
        if (c.slug or "").strip()
    }

    report = GcReport(applied=apply, older_than_days=older_than_days)
    report.aged_deleted = purge_aged_deleted(
        raw_dir, older_than_days=older_than_days, apply=apply, now=now
    )

    orphan_hub_paths = find_orphan_hubs(hub_dir, live_slugs)
    report.orphan_hubs = [str(p.relative_to(hub_dir)) for p in orphan_hub_paths]
    if apply:
        for p in orphan_hub_paths:
            shutil.rmtree(p)

    orphan_moc_paths = find_orphan_mocs(hub_dir, referenced_mocs_for(registry))
    report.orphan_mocs = [p.name for p in orphan_moc_paths]
    if apply:
        for p in orphan_moc_paths:
            p.unlink()

    if strip_parents:
        report.hub_parents_stripped = strip_hub_parents(raw_dir, apply=apply)

    return report
