from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from research_hub.clusters import ClusterRegistry
from research_hub.dashboard import generate_dashboard
from research_hub import cli
from research_hub.vault.graph_config import (
    PALETTE,
    build_color_groups,
    build_label_color_groups,
    refresh_graph_from_vault,
)


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    research_hub_dir = root / ".research_hub"
    obsidian = root / ".obsidian"
    raw.mkdir(parents=True)
    research_hub_dir.mkdir(parents=True)
    obsidian.mkdir(parents=True)
    (obsidian / "graph.json").write_text(json.dumps({"showTags": False}), encoding="utf-8")
    return SimpleNamespace(
        root=root,
        raw=raw,
        hub=root / "hub",
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
    )


def test_build_cluster_color_groups_has_palette_a():
    groups = build_color_groups(["alpha", "beta", "gamma"])
    assert [group["query"] for group in groups] == [
        "path:raw/alpha/",
        "path:raw/beta/",
        "path:raw/gamma/",
    ]
    assert groups[0]["color"]["rgb"] != groups[1]["color"]["rgb"]
    assert build_color_groups([f"c{i}" for i in range(len(PALETTE) + 1)])[0]["color"]["rgb"] == build_color_groups(
        [f"c{i}" for i in range(len(PALETTE) + 1)]
    )[len(PALETTE)]["color"]["rgb"]


def test_build_label_color_groups_has_all_canonical_labels():
    from research_hub.paper import CANONICAL_LABELS

    groups = build_label_color_groups()
    assert len(groups) == len(CANONICAL_LABELS)
    for label in CANONICAL_LABELS:
        assert any(group["query"] == f"tag:#label/{label}" for group in groups)


def test_refresh_graph_no_label_tags_emits_only_cluster_groups(tmp_path: Path):
    """P2-5a: with 0 notes carrying `#label/` tags, `--refresh` emits NO label
    color groups. The old behavior re-injected all 9 canonical label groups on
    every refresh, so a user who deleted the dead ones (showTags:false, 0
    labelled notes) got them back every time and could not keep them deleted."""
    cfg = _cfg(tmp_path)
    ClusterRegistry(cfg.clusters_file).create(query="alpha", name="Alpha", slug="alpha")
    ClusterRegistry(cfg.clusters_file).create(query="beta", name="Beta", slug="beta")

    count = refresh_graph_from_vault(cfg)

    data = json.loads((cfg.root / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    queries = [group["query"] for group in data["colorGroups"]]
    assert count == 2  # 2 cluster groups, 0 label groups
    assert "path:raw/alpha/" in queries
    assert "path:raw/beta/" in queries
    assert not any(q.startswith("tag:#label/") for q in queries)


def test_refresh_graph_emits_label_groups_only_for_present_tags(tmp_path: Path):
    """When notes DO carry `#label/<x>` tags, refresh emits a color group for
    each present label — and only those (absent labels stay out)."""
    cfg = _cfg(tmp_path)
    ClusterRegistry(cfg.clusters_file).create(query="alpha", name="Alpha", slug="alpha")
    note_dir = cfg.raw / "alpha"
    note_dir.mkdir(parents=True, exist_ok=True)
    (note_dir / "paper.md").write_text(
        '---\ntopic_cluster: "alpha"\n---\nBody\n\n#label/seed #label/method\n',
        encoding="utf-8",
    )

    refresh_graph_from_vault(cfg)

    data = json.loads((cfg.root / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    queries = [group["query"] for group in data["colorGroups"]]
    assert "path:raw/alpha/" in queries
    assert "tag:#label/seed" in queries
    assert "tag:#label/method" in queries
    assert "tag:#label/archived" not in queries  # absent label → no group
    # residue under raw/_deleted_* must not resurrect a label group
    deleted = cfg.raw / "_deleted_gone"
    deleted.mkdir(parents=True, exist_ok=True)
    (deleted / "old.md").write_text(
        '---\ntopic_cluster: "gone"\n---\nBody\n\n#label/archived\n', encoding="utf-8"
    )
    refresh_graph_from_vault(cfg)
    data2 = json.loads((cfg.root / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    q2 = [group["query"] for group in data2["colorGroups"]]
    assert "tag:#label/archived" not in q2


def test_refresh_graph_preserves_user_color_groups(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ClusterRegistry(cfg.clusters_file).create(query="alpha", name="Alpha", slug="alpha")
    graph_path = cfg.root / ".obsidian" / "graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "showTags": False,
                "colorGroups": [
                    {"query": "tag:#custom", "color": {"a": 1, "rgb": 123}},
                    {"query": "path:raw/old/", "color": {"a": 1, "rgb": 456}},
                ],
            }
        ),
        encoding="utf-8",
    )

    refresh_graph_from_vault(cfg)

    data = json.loads(graph_path.read_text(encoding="utf-8"))
    queries = [group["query"] for group in data["colorGroups"]]
    assert "tag:#custom" in queries
    assert "path:raw/old/" not in queries


def test_refresh_graph_idempotent_when_unchanged(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ClusterRegistry(cfg.clusters_file).create(query="alpha", name="Alpha", slug="alpha")
    graph_path = cfg.root / ".obsidian" / "graph.json"

    refresh_graph_from_vault(cfg)
    first_mtime = graph_path.stat().st_mtime_ns
    refresh_graph_from_vault(cfg)
    second_mtime = graph_path.stat().st_mtime_ns

    assert first_mtime == second_mtime


def test_cluster_create_triggers_refresh(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    spied: list[object] = []
    monkeypatch.setattr("research_hub.clusters.get_config", lambda: cfg)
    monkeypatch.setattr(
        "research_hub.vault.graph_config.refresh_graph_from_vault",
        lambda passed_cfg: spied.append(passed_cfg) or 0,
    )

    ClusterRegistry(cfg.clusters_file).create(query="alpha", name="Alpha", slug="alpha")

    assert spied == [cfg]


def test_cluster_delete_triggers_refresh(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("research_hub.clusters.get_config", lambda: cfg)
    spied: list[object] = []
    monkeypatch.setattr(
        "research_hub.vault.graph_config.refresh_graph_from_vault",
        lambda passed_cfg: spied.append(passed_cfg) or 0,
    )
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create(query="alpha", name="Alpha", slug="alpha")
    spied.clear()
    cluster_dir = cfg.raw / "alpha"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "paper.md").write_text('---\ntopic_cluster: "alpha"\n---\nBody\n', encoding="utf-8")

    registry.delete("alpha")

    assert spied == [cfg]


def test_dashboard_command_triggers_refresh(tmp_path: Path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    monkeypatch.setattr("research_hub.dashboard.generate_dashboard", lambda **_: cfg.research_hub_dir / "dashboard.html")
    spied: list[object] = []
    monkeypatch.setattr(
        "research_hub.vault.graph_config.refresh_graph_from_vault",
        lambda passed_cfg: spied.append(passed_cfg) or 11,
    )

    rc = cli.main(["dashboard"])

    assert rc == 0
    assert spied == [cfg]
    assert "Graph colors refreshed (11 groups)" in capsys.readouterr().out


def test_vault_graph_colors_refresh_command(tmp_path: Path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    monkeypatch.setattr(
        "research_hub.vault.graph_config.refresh_graph_from_vault",
        lambda passed_cfg: 12,
    )

    rc = cli.main(["vault", "graph-colors", "--refresh"])

    assert rc == 0
    assert "Refreshed graph colors: 12 groups" in capsys.readouterr().out
