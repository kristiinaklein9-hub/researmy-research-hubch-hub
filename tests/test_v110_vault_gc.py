"""v1.1 P2-5d — vault garbage collection.

Covers the four passes (aged _deleted_ purge, orphan hub removal, orphan _moc
removal, paper-note Hub-block bare-parent strip) plus the run_gc orchestrator
and the CLI surface. Destructive tool → every pass is tested dry-run-safe AND
apply-correct, and the v1.0.7 GC invariant (PARENT_MOCS never removed) is pinned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from research_hub.clusters import Cluster, ClusterRegistry
from research_hub.vault.gc import (
    find_orphan_hubs,
    find_orphan_mocs,
    purge_aged_deleted,
    referenced_mocs_for,
    run_gc,
    strip_hub_parents,
)


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    hub = root / "hub"
    rh = root / ".research_hub"
    for p in (raw, hub, rh):
        p.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(root=root, raw=raw, hub=hub, clusters_file=rh / "clusters.yaml")


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


# --------------------------------------------------------------------------- #
# Pass 1: aged _deleted_ purge
# --------------------------------------------------------------------------- #
def test_purge_aged_deleted_removes_aged_but_never_live(tmp_path):
    cfg = _cfg(tmp_path)
    aged = cfg.raw / "_deleted_old"
    aged.mkdir()
    (aged / "p.md").write_text("x", encoding="utf-8")
    live = cfg.raw / "real-cluster"  # NOT a _deleted_ dir
    live.mkdir()
    (live / "paper.md").write_text("live", encoding="utf-8")

    now = _mtime(aged) + timedelta(days=40)
    actions = purge_aged_deleted(cfg.raw, older_than_days=30, apply=True, now=now)

    assert {a["name"] for a in actions} == {"_deleted_old"}
    # The cardinal guarantee: a live cluster dir is NEVER a purge candidate...
    assert live.exists()
    assert (live / "paper.md").exists()
    # ...and the aged residue is gone after apply.
    assert not aged.exists()


def test_purge_aged_deleted_dry_run_reports_without_deleting(tmp_path):
    cfg = _cfg(tmp_path)
    aged = cfg.raw / "_deleted_old"
    aged.mkdir()
    now = _mtime(aged) + timedelta(days=40)

    actions = purge_aged_deleted(cfg.raw, older_than_days=30, apply=False, now=now)

    assert len(actions) == 1
    assert aged.exists()  # dry-run never deletes


def test_purge_aged_deleted_keeps_young_residue(tmp_path):
    cfg = _cfg(tmp_path)
    young = cfg.raw / "_deleted_recent"
    young.mkdir()
    now = _mtime(young) + timedelta(days=5)  # only 5d old, threshold 30

    actions = purge_aged_deleted(cfg.raw, older_than_days=30, apply=True, now=now)

    assert actions == []
    assert young.exists()


# --------------------------------------------------------------------------- #
# Pass 2: orphan hub removal
# --------------------------------------------------------------------------- #
def test_find_orphan_hubs_skips_live_and_reserved(tmp_path):
    cfg = _cfg(tmp_path)
    (cfg.hub / "live-cluster").mkdir()
    (cfg.hub / "ghost-cluster").mkdir()
    (cfg.hub / "_moc").mkdir()
    (cfg.hub / "_archived").mkdir()

    orphans = find_orphan_hubs(cfg.hub, live_slugs={"live-cluster"})

    assert [p.name for p in orphans] == ["ghost-cluster"]


# --------------------------------------------------------------------------- #
# Pass 3: orphan _moc removal + PARENT_MOCS protection
# --------------------------------------------------------------------------- #
def test_find_orphan_mocs_protects_parent_mocs(tmp_path):
    cfg = _cfg(tmp_path)
    moc_dir = cfg.hub / "_moc"
    moc_dir.mkdir()
    for name in ("LLM-Agents", "LLM-Agents-Human", "Stale-Topic"):
        (moc_dir / f"{name}.md").write_text("# moc", encoding="utf-8")

    # Only the sub-MOC is "referenced"; the parent must still be protected, and
    # only the unreferenced non-parent page is an orphan.
    orphans = find_orphan_mocs(cfg.hub, referenced_mocs={"LLM-Agents-Human"})

    assert [p.name for p in orphans] == ["Stale-Topic.md"]


def test_referenced_mocs_includes_parents_and_sub_mocs(tmp_path):
    cfg = _cfg(tmp_path)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters["social-llm-agents"] = Cluster(slug="social-llm-agents", name="Social")
    registry.save()

    refs = referenced_mocs_for(registry)

    assert "LLM-Agents" in refs            # parent always present
    assert "Water-Resources" in refs       # both family parents protected
    assert "LLM-Agents-Social" in refs     # the cluster's sub-MOC


def test_referenced_mocs_uses_query_not_just_slug(tmp_path):
    """P1 regression: a cluster whose SLUG lacks a family keyword but whose
    first_query carries one still owns a query-derived sub-MOC (ingest creates it
    from BOTH). referenced_mocs_for MUST include it, or `vault gc` would delete a
    LIVE sub-MOC that populate_all_mocs never regenerates (over-deletion bug)."""
    cfg = _cfg(tmp_path)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters["ml-forecasting-coastal"] = Cluster(
        slug="ml-forecasting-coastal",
        name="ML Forecasting",
        first_query="flood inundation modeling",  # keyword is in the QUERY only
    )
    registry.save()

    refs = referenced_mocs_for(registry)

    # The query routes to Water-Resources; the sub-MOC is slug-derived.
    assert "Water-Resources" in refs
    assert any(r.startswith("Water-Resources-") for r in refs), refs

    # End-to-end: that query-derived sub-MOC page must NOT be flagged orphan.
    moc_dir = cfg.hub / "_moc"
    moc_dir.mkdir(parents=True, exist_ok=True)
    sub = sorted(r for r in refs if r.startswith("Water-Resources-"))[0]
    (moc_dir / f"{sub}.md").write_text("# moc", encoding="utf-8")
    (moc_dir / "Genuinely-Orphan.md").write_text("# moc", encoding="utf-8")
    orphans = find_orphan_mocs(cfg.hub, refs)
    assert [p.name for p in orphans] == ["Genuinely-Orphan.md"]


# --------------------------------------------------------------------------- #
# Pass 4: paper-note Hub-block bare-parent strip
# --------------------------------------------------------------------------- #
def _paper_with_hub(slug_dir: Path, name: str) -> Path:
    p = slug_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\ntopic_cluster: c\n---\n"
        "## Hub\n\n"
        "- Cluster: [[c/00_overview|c]]\n"
        "- MOC: [[LLM-Agents]]\n"
        "- MOC: [[LLM-Agents-Human]]\n"
        "- MOC: [[Water-Resources]]\n"
        "- MOC: [[Water-Resources-Human]]\n\n"
        "## Notes\nbody\n",
        encoding="utf-8",
    )
    return p


def test_strip_hub_parents_drops_bare_parent_keeps_sub_moc(tmp_path):
    cfg = _cfg(tmp_path)
    note = _paper_with_hub(cfg.raw / "c", "p1.md")

    changed = strip_hub_parents(cfg.raw, apply=True)

    assert changed == ["c/p1.md"] or changed == [str(Path("c") / "p1.md")]
    text = note.read_text(encoding="utf-8")
    assert "[[LLM-Agents]]" not in text
    assert "[[Water-Resources]]" not in text
    # Sub-MOCs survive.
    assert "[[LLM-Agents-Human]]" in text
    assert "[[Water-Resources-Human]]" in text
    # Cluster line + content untouched.
    assert "[[c/00_overview|c]]" in text
    assert "## Notes" in text


def test_strip_hub_parents_dry_run_does_not_write(tmp_path):
    cfg = _cfg(tmp_path)
    note = _paper_with_hub(cfg.raw / "c", "p1.md")
    before = note.read_text(encoding="utf-8")

    changed = strip_hub_parents(cfg.raw, apply=False)

    assert len(changed) == 1
    assert note.read_text(encoding="utf-8") == before  # reported, not written


def test_strip_hub_parents_skips_deleted_residue(tmp_path):
    cfg = _cfg(tmp_path)
    dead = _paper_with_hub(cfg.raw / "_deleted_c", "old.md")
    before = dead.read_text(encoding="utf-8")

    changed = strip_hub_parents(cfg.raw, apply=True)

    assert changed == []
    assert dead.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# Orchestrator + CLI
# --------------------------------------------------------------------------- #
def test_run_gc_dry_run_reports_all_passes_without_mutating(tmp_path):
    cfg = _cfg(tmp_path)
    aged = cfg.raw / "_deleted_old"
    aged.mkdir()
    (cfg.hub / "ghost").mkdir()
    moc_dir = cfg.hub / "_moc"
    moc_dir.mkdir()
    (moc_dir / "Stale.md").write_text("x", encoding="utf-8")
    note = _paper_with_hub(cfg.raw / "c", "p1.md")
    now = _mtime(aged) + timedelta(days=40)

    report = run_gc(cfg, older_than_days=30, apply=False, now=now)

    assert len(report.aged_deleted) == 1
    assert report.orphan_hubs == ["ghost"]
    assert "Stale.md" in report.orphan_mocs
    assert len(report.hub_parents_stripped) == 1
    assert report.total_actions() == 4
    # Nothing mutated in dry-run.
    assert aged.exists()
    assert (cfg.hub / "ghost").exists()
    assert (moc_dir / "Stale.md").exists()
    assert "[[LLM-Agents]]" in note.read_text(encoding="utf-8")


def test_run_gc_apply_executes_all_passes(tmp_path):
    cfg = _cfg(tmp_path)
    aged = cfg.raw / "_deleted_old"
    aged.mkdir()
    (cfg.hub / "ghost").mkdir()
    moc_dir = cfg.hub / "_moc"
    moc_dir.mkdir()
    (moc_dir / "Stale.md").write_text("x", encoding="utf-8")
    # A protected parent MOC must survive even with zero clusters.
    (moc_dir / "LLM-Agents.md").write_text("x", encoding="utf-8")
    note = _paper_with_hub(cfg.raw / "c", "p1.md")
    now = _mtime(aged) + timedelta(days=40)

    report = run_gc(cfg, older_than_days=30, apply=True, now=now)

    assert report.applied is True
    assert not aged.exists()
    assert not (cfg.hub / "ghost").exists()
    assert not (moc_dir / "Stale.md").exists()
    assert (moc_dir / "LLM-Agents.md").exists()  # PARENT_MOCS protected
    assert "[[LLM-Agents]]" not in note.read_text(encoding="utf-8")
    assert "[[LLM-Agents-Human]]" in note.read_text(encoding="utf-8")


def test_run_gc_keeps_moc_still_linked_by_a_live_note(tmp_path):
    """Regression (found on the real vault during the v1.1 graph migration): a
    `_moc` page no cluster DERIVES but a live note still LINKS must NOT be
    deleted — doing so would strand dangling wikilinks. gc must never make the
    graph worse (the stale link is a separate, deferred cleanup)."""
    cfg = _cfg(tmp_path)
    moc_dir = cfg.hub / "_moc"
    moc_dir.mkdir(parents=True, exist_ok=True)
    # No cluster derives this sub-MOC, but a live note links it (stale rebind).
    (moc_dir / "LLM-Agents-StaleButLinked.md").write_text("# moc", encoding="utf-8")
    (moc_dir / "LLM-Agents-TrulyOrphan.md").write_text("# moc", encoding="utf-8")
    note = cfg.raw / "some-cluster" / "p1.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntopic_cluster: some-cluster\n---\n## Hub\n\n- MOC: [[LLM-Agents-StaleButLinked]]\n",
        encoding="utf-8",
    )

    report = run_gc(cfg, apply=True, strip_parents=False)

    # The linked one survives; the truly-unreferenced one is removed.
    assert "LLM-Agents-StaleButLinked.md" not in report.orphan_mocs
    assert (moc_dir / "LLM-Agents-StaleButLinked.md").exists()
    assert "LLM-Agents-TrulyOrphan.md" in report.orphan_mocs
    assert not (moc_dir / "LLM-Agents-TrulyOrphan.md").exists()


def test_run_gc_keeps_hub_still_linked_by_a_live_note(tmp_path):
    """Same data-corruption class as the _moc guard: a `hub/<slug>/` with no
    registry entry that a live note links into (`[[ghost/00_overview]]`) must NOT
    be removed — deleting it would strand dangling wikilinks."""
    cfg = _cfg(tmp_path)
    (cfg.hub / "ghost-but-linked").mkdir(parents=True, exist_ok=True)
    (cfg.hub / "truly-orphan-hub").mkdir(parents=True, exist_ok=True)
    note = cfg.raw / "some-cluster" / "p1.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntopic_cluster: some-cluster\n---\n"
        "## Hub\n\n- Cluster: [[ghost-but-linked/00_overview|ghost-but-linked]]\n",
        encoding="utf-8",
    )

    report = run_gc(cfg, apply=True, strip_parents=False)

    assert "ghost-but-linked" not in report.orphan_hubs
    assert (cfg.hub / "ghost-but-linked").exists()
    assert "truly-orphan-hub" in report.orphan_hubs
    assert not (cfg.hub / "truly-orphan-hub").exists()


def test_run_gc_no_strip_parents_skips_content_rewrite(tmp_path):
    cfg = _cfg(tmp_path)
    note = _paper_with_hub(cfg.raw / "c", "p1.md")

    report = run_gc(cfg, apply=True, strip_parents=False)

    assert report.hub_parents_stripped == []
    assert "[[LLM-Agents]]" in note.read_text(encoding="utf-8")  # untouched


def test_cli_vault_gc_dry_run_then_apply(tmp_path, monkeypatch, capsys):
    from research_hub import cli

    cfg = _cfg(tmp_path)
    # cli.py rebinds cli_vault.get_config to a forwarder lambda that delegates to
    # research_hub.cli.get_config (the _sync_cli_dependencies shim), so THAT is
    # the canonical patch target — patching cli_vault.get_config is overwritten.
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    (cfg.hub / "ghost").mkdir()

    rc = cli.main(["vault", "gc"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run" in out
    assert "Would remove 1 orphan hub dir(s)" in out
    assert (cfg.hub / "ghost").exists()  # dry-run kept it

    rc2 = cli.main(["vault", "gc", "--apply"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "applied" in out2
    assert not (cfg.hub / "ghost").exists()
