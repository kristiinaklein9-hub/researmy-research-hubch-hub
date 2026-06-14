"""v1.1 P2-1 — the 6 archive/unarchive/delete/move-paper sites route through
``fsops.robust_move`` (retry/backoff on transient Windows lock errors).

The unit contract of ``robust_move`` itself lives in
``test_v1_rebind_move_robustness.py``; these tests prove each SITE is wired to it
by injecting a transient lock on the move and asserting the operation self-heals.

ISOLATION NOTE: ``research_hub.clusters`` / ``research_hub.paper`` are
late-imported INSIDE each test (not at module top) and patched via the module
OBJECT. An earlier suite (``test_v033_workflows``) pops these modules from
``sys.modules`` via its autouse re-import fixture, so a module-top import would
bind a STALE module object while a string-form ``monkeypatch.setattr`` would
patch the freshly-reloaded one — the patch would silently miss and the op would
run against the real (leaked) config. Late import keeps both on the same object.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import research_hub.fsops as fsops


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(fsops.time, "sleep", lambda *_a, **_k: None)


class _LockMove:
    """A shutil.move stub that fails for matching destinations."""

    def __init__(self, predicate, *, times=None):
        self._real = shutil.move
        self._predicate = predicate
        self._times = times
        self._failures = 0
        self.calls = 0

    def __call__(self, src, dst, *args, **kwargs):
        self.calls += 1
        if self._predicate(str(dst)):
            if self._times is None or self._failures < self._times:
                self._failures += 1
                raise PermissionError(
                    f"[WinError 32] simulated sharing violation writing {dst}"
                )
        return self._real(src, dst, *args, **kwargs)


def _clusters():
    """Freshly resolve research_hub.clusters from sys.modules (see ISOLATION NOTE)."""
    import research_hub.clusters as c

    return c


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    hub = root / "hub"
    research_hub_dir = root / ".research_hub"
    for path in (raw, hub, research_hub_dir):
        path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        raw=raw,
        hub=hub,
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
    )


def _registry(cfg, *clusters):
    c = _clusters()
    registry = c.ClusterRegistry(cfg.clusters_file)
    for cluster in clusters:
        registry.clusters[cluster.slug] = cluster
    registry.save()
    return registry


def _destination_is(expected: Path):
    expected_resolved = expected.resolve()

    def predicate(dst: str) -> bool:
        return Path(dst).resolve() == expected_resolved

    return predicate


def _seed_hub_dir(cfg, slug: str) -> Path:
    hub_dir = cfg.hub / slug
    hub_dir.mkdir(parents=True, exist_ok=True)
    (hub_dir / "00_overview.md").write_text("# Overview\n", encoding="utf-8")
    return hub_dir


def _seed_raw_dir(cfg, slug: str) -> Path:
    raw_dir = cfg.raw / slug
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "paper-one.md").write_text(
        "---\ntopic_cluster: " + slug + "\n---\n",
        encoding="utf-8",
    )
    return raw_dir


def test_cluster_archive_recovers_from_transient_lock(tmp_path, monkeypatch):
    c = _clusters()
    cfg = _cfg(tmp_path)
    slug = "alpha"
    _registry(cfg, c.Cluster(slug=slug, name="Alpha"))
    hub_dir = _seed_hub_dir(cfg, slug)
    archived_dir = cfg.hub / "_archived" / slug
    monkeypatch.setattr(c, "get_config", lambda: cfg)

    stub = _LockMove(_destination_is(archived_dir), times=1)
    monkeypatch.setattr(fsops.shutil, "move", stub)

    cluster = c.ClusterRegistry(cfg.clusters_file).archive(slug)

    assert stub._failures == 1
    assert cluster.status == "archived"
    assert not hub_dir.exists()
    assert (archived_dir / "00_overview.md").exists()
    assert c.ClusterRegistry(cfg.clusters_file).get(slug).status == "archived"


def test_cluster_unarchive_recovers_from_transient_lock(tmp_path, monkeypatch):
    c = _clusters()
    cfg = _cfg(tmp_path)
    slug = "alpha"
    _registry(
        cfg,
        c.Cluster(
            slug=slug,
            name="Alpha",
            status="archived",
            archived_at="2026-06-01T00:00:00Z",
        ),
    )
    archived_dir = cfg.hub / "_archived" / slug
    archived_dir.mkdir(parents=True, exist_ok=True)
    (archived_dir / "00_overview.md").write_text("# Overview\n", encoding="utf-8")
    hub_dir = cfg.hub / slug
    monkeypatch.setattr(c, "get_config", lambda: cfg)

    stub = _LockMove(_destination_is(hub_dir), times=1)
    monkeypatch.setattr(fsops.shutil, "move", stub)

    cluster = c.ClusterRegistry(cfg.clusters_file).unarchive(slug)

    assert stub._failures == 1
    assert cluster.status == "active"
    assert cluster.archived_at == ""
    assert (hub_dir / "00_overview.md").exists()
    assert not archived_dir.exists()


def test_cluster_delete_soft_move_recovers_from_transient_lock(tmp_path, monkeypatch):
    c = _clusters()
    cfg = _cfg(tmp_path)
    slug = "alpha"
    _registry(cfg, c.Cluster(slug=slug, name="Alpha"))
    raw_dir = _seed_raw_dir(cfg, slug)
    _seed_hub_dir(cfg, slug)
    deleted_dir = cfg.raw / f"_deleted_{slug}"
    monkeypatch.setattr(c, "get_config", lambda: cfg)

    stub = _LockMove(_destination_is(deleted_dir), times=1)
    monkeypatch.setattr(fsops.shutil, "move", stub)

    c.cascade_delete_cluster(cfg, slug, apply=True)

    assert stub._failures == 1
    assert not raw_dir.exists()
    assert (deleted_dir / "paper-one.md").exists()
    assert c.ClusterRegistry(cfg.clusters_file).get(slug) is None


def test_cluster_delete_soft_move_reraises_persistent_lock(tmp_path, monkeypatch):
    c = _clusters()
    cfg = _cfg(tmp_path)
    slug = "alpha"
    _registry(cfg, c.Cluster(slug=slug, name="Alpha"))
    raw_dir = _seed_raw_dir(cfg, slug)
    hub_dir = _seed_hub_dir(cfg, slug)
    deleted_dir = cfg.raw / f"_deleted_{slug}"
    monkeypatch.setattr(c, "get_config", lambda: cfg)

    stub = _LockMove(_destination_is(deleted_dir), times=None)
    monkeypatch.setattr(fsops.shutil, "move", stub)

    with pytest.raises(PermissionError):
        c.cascade_delete_cluster(cfg, slug, apply=True)

    assert stub._failures == fsops._MOVE_RETRY_ATTEMPTS
    assert raw_dir.exists()
    assert not deleted_dir.exists()
    assert hub_dir.exists()
    assert c.ClusterRegistry(cfg.clusters_file).get(slug) is not None


def test_paper_bulk_move_routes_through_robust_move(tmp_path, monkeypatch):
    import research_hub.paper as paper

    c = _clusters()
    cfg = _cfg(tmp_path)
    _registry(
        cfg,
        c.Cluster(slug="source", name="Source"),
        c.Cluster(slug="target", name="Target"),
    )
    source = cfg.raw / "source" / "paper-one.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "---\ntopic_cluster: source\nlabels: [core]\n---\nBody\n",
        encoding="utf-8",
    )
    target = cfg.raw / "target" / "paper-one.md"
    calls: list[tuple[str, str]] = []
    real_move = shutil.move

    def spy(src: str, dst: str) -> None:
        calls.append((src, dst))
        real_move(src, dst)

    monkeypatch.setattr(paper, "robust_move", spy)
    monkeypatch.setattr(paper, "_rebuild_dedup_index", lambda _cfg: None)

    result = paper.bulk_move(cfg, ["paper-one"], "target", dry_run=False)

    assert calls == [(str(source), str(target))]
    assert result["moved"] == ["paper-one"]
    assert not source.exists()
    assert target.exists()
