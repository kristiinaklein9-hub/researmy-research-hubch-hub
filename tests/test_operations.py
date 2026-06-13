from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_hub.clusters import ClusterRegistry
from research_hub.config import get_config
from research_hub.dedup import DedupHit, DedupIndex
from research_hub.operations import mark_paper, move_paper, remove_paper


def _make_config(tmp_path: Path, monkeypatch):
    root = tmp_path / "vault"
    raw = root / "raw"
    hub_dir = root / ".research_hub"
    raw.mkdir(parents=True)
    hub_dir.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"knowledge_base": {"root": str(root), "raw": str(raw)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_HUB_CONFIG", str(config_path))
    return get_config()


def _write_note(
    path: Path,
    *,
    title: str,
    doi: str = "",
    cluster: str = "",
    status: str = "unread",
    zotero_key: str = "",
):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f'title: "{title}"\n'
        f'doi: "{doi}"\n'
        f'zotero-key: "{zotero_key}"\n'
        f'topic_cluster: "{cluster}"\n'
        f"status: {status}\n"
        "---\n"
        f"# {title}\n",
        encoding="utf-8",
    )
    return path


def test_remove_paper_removes_matching_note(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    note = _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One")

    result = remove_paper("paper-one")

    assert result["removed_files"] == [str(note)]
    assert not note.exists()


def test_remove_paper_dry_run_keeps_file(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    note = _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One")

    result = remove_paper("paper-one", dry_run=True)

    assert result["removed_files"] == [str(note)]
    assert note.exists()


def test_remove_paper_resolves_doi_from_dedup_index(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    note = _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One", doi="10.1000/example")
    index = DedupIndex()
    index.add(
        DedupHit(
            source="obsidian",
            doi="10.1000/example",
            title="Paper One",
            obsidian_path=str(note),
        )
    )
    index.save(cfg.research_hub_dir / "dedup_index.json")

    result = remove_paper("10.1000/example")

    assert result["removed_files"] == [str(note)]
    assert not note.exists()


def test_remove_paper_deletes_zotero_item_when_requested(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    _write_note(
        cfg.raw / "alpha" / "paper-one.md",
        title="Paper One",
        zotero_key="ABCD1234",
    )
    calls: list[str] = []

    class FakeDual:
        def delete_item(self, key):
            calls.append(key)

    monkeypatch.setattr("research_hub.zotero.client.ZoteroDualClient", lambda: FakeDual())

    result = remove_paper("paper-one", include_zotero=True)

    assert result["zotero_deleted"] is True
    assert calls == ["ABCD1234"]


def test_mark_paper_updates_status(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    note = _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One", status="unread")

    result = mark_paper("paper-one", "reading")

    assert result["updated"] == [str(note)]
    assert "status: reading" in note.read_text(encoding="utf-8")


def test_mark_paper_bulk_marks_cluster(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    note1 = _write_note(cfg.raw / "alpha" / "one.md", title="One", cluster="alpha")
    note2 = _write_note(cfg.raw / "alpha" / "two.md", title="Two", cluster="alpha")

    result = mark_paper(None, "deep-read", cluster="alpha")

    assert result["updated"] == [str(note1), str(note2)]
    assert "status: deep-read" in note1.read_text(encoding="utf-8")
    assert "status: deep-read" in note2.read_text(encoding="utf-8")


def test_mark_paper_rejects_invalid_status(tmp_path, monkeypatch):
    _make_config(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        mark_paper("paper-one", "skim")


def test_move_paper_moves_file_and_updates_cluster(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    source = _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One", cluster="alpha")

    result = move_paper("paper-one", "beta")
    target = cfg.raw / "beta" / "paper-one.md"

    # move_paper now returns a richer dict (deep sync: body refs / footers / dedup),
    # so assert the core contract fields rather than exact equality.
    assert result["from"] == str(source)
    assert result["to"] == str(target)
    assert result["cluster"] == "beta"
    assert result["old_cluster"] == "alpha"
    assert not source.exists()
    assert target.exists()
    assert "topic_cluster: beta" in target.read_text(encoding="utf-8")


def test_move_paper_creates_target_directory(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One", cluster="alpha")

    move_paper("paper-one", "new-cluster")

    assert (cfg.raw / "new-cluster").exists()


def test_move_paper_raises_when_source_missing(tmp_path, monkeypatch):
    _make_config(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError):
        move_paper("missing", "beta")


def test_move_paper_noop_when_already_in_target(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    note = _write_note(cfg.raw / "beta" / "paper-one.md", title="Paper One", cluster="beta")

    result = move_paper("paper-one", "beta")

    # No-op move now returns the full dict shape (W-1 fix) so callers can always
    # index old_cluster / sync_warnings / dedup_* without a KeyError.
    assert result["from"] == str(note)
    assert result["to"] == str(note)
    assert result["cluster"] == "beta"
    assert result["old_cluster"] == "beta"
    assert result["frontmatter_updated"] is False
    assert result["dedup_synced"] is False
    assert result["sync_warnings"] == []
    assert note.exists()


def test_cluster_rename_updates_registry(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("flood risk", name="Flood Risk", slug="flood-risk")

    updated = registry.rename("flood-risk", "Flood Perception")

    assert updated.name == "Flood Perception"
    assert ClusterRegistry(cfg.clusters_file).get("flood-risk").name == "Flood Perception"


def test_cluster_rename_updates_display_name_only(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("my-query", name="Old Name", slug="my-slug")

    registry.rename("my-slug", "New Name")

    fresh = ClusterRegistry(cfg.clusters_file)
    assert fresh.clusters["my-slug"].name == "New Name"


def test_cluster_delete_removes_registry_entry_and_unbinds_notes(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("flood risk", name="Flood Risk", slug="flood-risk")
    note = _write_note(cfg.raw / "flood-risk" / "paper-one.md", title="Paper One", cluster="flood-risk")

    result = registry.delete("flood-risk")

    assert result["notes_unbound"] == 1
    assert ClusterRegistry(cfg.clusters_file).get("flood-risk") is None
    assert 'topic_cluster: ""' in note.read_text(encoding="utf-8")


def test_cluster_delete_dry_run_preserves_registry(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("flood risk", name="Flood Risk", slug="flood-risk")

    result = registry.delete("flood-risk", dry_run=True)

    assert result["dry_run"] is True
    assert ClusterRegistry(cfg.clusters_file).get("flood-risk") is not None


def test_cluster_merge_moves_files_and_updates_notes(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("source", name="Source", slug="source")
    registry.create("target", name="Target", slug="target")
    note = _write_note(cfg.raw / "source" / "paper-one.md", title="Paper One", cluster="source")

    result = registry.merge("source", "target", vault_raw=cfg.raw)
    moved = cfg.raw / "target" / "paper-one.md"

    assert result == {"source": "source", "target": "target", "moved": 1}
    # merge now leaves a tombstone (status=merged + merged_into) instead of
    # dropping the entry, so a re-ingest on the source seed redirects to target
    # rather than re-creating the merged-away cluster (the duplicate-cluster fix).
    fresh = ClusterRegistry(cfg.clusters_file)
    src = fresh.get("source")
    assert src is not None and src.status == "merged" and src.merged_into == "target"
    assert "source" not in {c.slug for c in fresh.list()}  # hidden from the active set
    assert fresh.resolve_merged("source").slug == "target"  # tombstone resolves to target
    assert moved.exists()
    assert "topic_cluster: target" in moved.read_text(encoding="utf-8")
    assert not note.exists()


def test_cluster_merge_rolls_back_on_move_failure(tmp_path, monkeypatch):
    """A mid-merge move failure leaves the source ACTIVE (not tombstoned) and
    rolls the moved notes back — never a half-merge / stranded paper (the
    Windows transient-lock data-integrity class the v1.0.7 keystone closes)."""
    from pathlib import Path

    import research_hub.operations as ops

    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("source", name="Source", slug="source")
    registry.create("target", name="Target", slug="target")
    _write_note(cfg.raw / "source" / "p1.md", title="P1", cluster="source")
    _write_note(cfg.raw / "source" / "p2.md", title="P2", cluster="source")

    real_move = ops.robust_move

    def flaky_move(src, dst):
        # Fail only when moving p2 INTO target; let p1's forward move + all
        # rollback moves (back to source) succeed.
        if "p2" in Path(src).name and "target" in Path(dst).parts:
            raise PermissionError("simulated transient lock")
        return real_move(src, dst)

    monkeypatch.setattr(ops, "robust_move", flaky_move)

    with pytest.raises(PermissionError):
        registry.merge("source", "target", vault_raw=cfg.raw)

    fresh = ClusterRegistry(cfg.clusters_file)
    src = fresh.get("source")
    assert src is not None and src.status == "active"      # NOT tombstoned
    assert src.merged_into == ""
    assert (cfg.raw / "source" / "p1.md").exists()         # rolled back
    assert (cfg.raw / "source" / "p2.md").exists()         # never left source
    assert not list((cfg.raw / "target").glob("*.md"))     # nothing stranded in target


def test_move_paper_syncs_dedup_index(tmp_path, monkeypatch):
    """move_paper invalidates the old obsidian_path in the persisted dedup index
    and registers the new one, so the index never goes stale on a move (the
    '126 stale' drift the v1.0.7 keystone closes)."""
    from research_hub.dedup import DedupHit, DedupIndex

    cfg = _make_config(tmp_path, monkeypatch)
    src = _write_note(cfg.raw / "alpha" / "paper-one.md", title="Paper One",
                      doi="10.1000/p1", cluster="alpha")
    idx_path = cfg.research_hub_dir / "dedup_index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    seed = DedupIndex.load(idx_path)  # empty when absent
    seed.add(DedupHit(source="obsidian", doi="10.1000/p1", title="Paper One",
                      zotero_key=None, obsidian_path=str(src)))
    seed.save(idx_path)

    move_paper("paper-one", "beta")

    new_path = str(cfg.raw / "beta" / "paper-one.md")
    assert DedupIndex.load(idx_path).invalidate_obsidian_path(str(src)) == 0   # old path gone
    assert DedupIndex.load(idx_path).invalidate_obsidian_path(new_path) >= 1   # new path present


def test_cluster_merge_tolerates_note_without_topic_cluster_frontmatter(tmp_path, monkeypatch):
    """A note missing the topic_cluster frontmatter key (or any frontmatter at
    all) must NOT trigger a spurious full rollback — the physical move is the
    integrity guarantee. The key is backfilled when a block is present (the C-1
    review fix: previously the over-strict verify rolled back a correct merge)."""
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("source", name="Source", slug="source")
    registry.create("target", name="Target", slug="target")

    # (a) frontmatter block present but topic_cluster key absent -> backfilled
    missing_field = cfg.raw / "source" / "p-missing-field.md"
    missing_field.parent.mkdir(parents=True, exist_ok=True)
    missing_field.write_text(
        '---\ntitle: "Missing Field"\ndoi: "10.1/mf"\n---\n# Missing Field\n',
        encoding="utf-8",
    )
    # (b) no frontmatter block at all -> verify tolerates the empty value
    no_frontmatter = cfg.raw / "source" / "p-no-fm.md"
    no_frontmatter.write_text("# No Frontmatter\n\nbody only\n", encoding="utf-8")

    result = registry.merge("source", "target", vault_raw=cfg.raw)

    assert result["moved"] == 2
    fresh = ClusterRegistry(cfg.clusters_file)
    src = fresh.get("source")
    assert src is not None and src.status == "merged" and src.merged_into == "target"
    assert (cfg.raw / "target" / "p-missing-field.md").exists()
    assert (cfg.raw / "target" / "p-no-fm.md").exists()
    assert not list((cfg.raw / "source").glob("*.md"))   # nothing stranded in source
    # the missing key was backfilled on the note that had a frontmatter block
    backfilled = (cfg.raw / "target" / "p-missing-field.md").read_text(encoding="utf-8")
    assert "topic_cluster: target" in backfilled


def test_cluster_merge_moves_the_correct_note_on_slug_collision(tmp_path, monkeypatch):
    """When the same slug exists in two clusters, merging one must move THAT
    cluster's note — never a same-slug note from another cluster. (P0: the
    adversarial review reproduced the old rglob[0] resolution silently moving
    the alphabetically-first duplicate and orphaning the real source note.)"""
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("aaaother", name="Other", slug="aaaother")
    registry.create("zzzsource", name="Source", slug="zzzsource")
    registry.create("target", name="Target", slug="target")

    # Same slug "paper" in two clusters; "aaaother" sorts before "zzzsource",
    # so the old vault-wide rglob[0] would have grabbed the WRONG (other) file.
    other = _write_note(cfg.raw / "aaaother" / "paper.md", title="Paper in Other",
                        doi="10.1/other", cluster="aaaother")
    src = _write_note(cfg.raw / "zzzsource" / "paper.md", title="Paper in Source",
                      doi="10.1/source", cluster="zzzsource")

    result = registry.merge("zzzsource", "target", vault_raw=cfg.raw)

    assert result["moved"] == 1
    moved = cfg.raw / "target" / "paper.md"
    assert moved.exists()
    assert "10.1/source" in moved.read_text(encoding="utf-8")   # the SOURCE paper moved
    assert other.exists()                                       # the other cluster untouched
    assert "10.1/other" in other.read_text(encoding="utf-8")
    assert not src.exists()                                     # source folder drained


def test_cluster_merge_gc_preserves_shared_parent_moc(tmp_path, monkeypatch):
    """Merging one LLM cluster must NOT delete the shared LLM-Agents parent MOC
    (the whole family links to it); only the source-only sub-MOC is GC'd. (P0
    review fix: the un-guarded GC permanently deleted the shared parent MOC.)"""
    from research_hub.vault.hub_overview import derive_moc_links

    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    src = registry.create("llm agent consumers", name="LLM Consumers", slug="llm-agent-consumers")
    tgt = registry.create("llm agent markets", name="LLM Markets", slug="llm-agent-markets")
    _write_note(cfg.raw / "llm-agent-consumers" / "p1.md", title="P1",
                doi="10.1/p1", cluster="llm-agent-consumers")

    moc_dir = cfg.hub / "_moc"
    moc_dir.mkdir(parents=True, exist_ok=True)
    src_links = derive_moc_links(src.slug, cluster_queries=[str(src.first_query or "")],
                                 moc_links=list(src.moc_links or []))
    tgt_links = derive_moc_links(tgt.slug, cluster_queries=[str(tgt.first_query or "")],
                                 moc_links=list(tgt.moc_links or []))
    for name in set(src_links) | set(tgt_links) | {"LLM-Agents"}:
        (moc_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    source_only = [n for n in src_links if n not in tgt_links and n != "LLM-Agents"]

    registry.merge("llm-agent-consumers", "llm-agent-markets", vault_raw=cfg.raw)

    assert (moc_dir / "LLM-Agents.md").exists()         # shared parent MOC protected
    assert source_only, "test setup: expected at least one source-only sub-MOC"
    for name in source_only:
        assert not (moc_dir / f"{name}.md").exists()    # source-only sub-MOC GC'd


def test_cluster_merge_raises_for_missing_target(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("source", name="Source", slug="source")

    with pytest.raises(ValueError):
        registry.merge("source", "missing", vault_raw=cfg.raw)


def test_cluster_merge_with_no_notes_returns_zero(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("source", name="Source", slug="source")
    registry.create("target", name="Target", slug="target")

    result = registry.merge("source", "target", vault_raw=cfg.raw)

    assert result["moved"] == 0


def test_cluster_split_creates_new_cluster_and_moves_matches(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("flood risk", name="Flood Risk", slug="flood-risk")
    _write_note(
        cfg.raw / "flood-risk" / "flood-agents.md",
        title="Flood Risk Agents",
        cluster="flood-risk",
    )
    _write_note(
        cfg.raw / "flood-risk" / "coastal-insurance.md",
        title="Coastal Insurance Pricing",
        cluster="flood-risk",
    )

    result = registry.split("flood-risk", "flood agents", "Flood Agents", vault_raw=cfg.raw)

    assert result["new_cluster"] == "flood-agents"
    assert result["moved"] == 1
    assert (cfg.raw / "flood-agents" / "flood-agents.md").exists()
    assert (cfg.raw / "flood-risk" / "coastal-insurance.md").exists()


def test_cluster_split_keeps_non_matching_notes(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, monkeypatch)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create("flood risk", name="Flood Risk", slug="flood-risk")
    _write_note(
        cfg.raw / "flood-risk" / "coastal-insurance.md",
        title="Coastal Insurance Pricing",
        cluster="flood-risk",
    )

    result = registry.split("flood-risk", "flood agents", "Flood Agents", vault_raw=cfg.raw)

    assert result["moved"] == 0
    assert result["remaining"] == 1


# ---------------------------------------------------------------------------
# remove_paper backward link cascade (v1.1.0)
# ---------------------------------------------------------------------------

def _write_note_with_links(path: Path, *, cluster: str, related: list[str]) -> None:
    """Write a note that already has a Related Papers section."""
    links = "\n".join(f"- [[{slug}]]" for slug in related)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f'title: "{path.stem}"\n'
        f'topic_cluster: "{cluster}"\n'
        "---\n\n"
        f"## Related Papers in This Cluster\n{links}\n",
        encoding="utf-8",
    )


def test_remove_paper_cleans_backward_wikilinks(tmp_path, monkeypatch):
    """After remove_paper, the removed slug disappears from sibling Related sections."""
    cfg = _make_config(tmp_path, monkeypatch)
    # Paper being removed
    _write_note(cfg.raw / "cluster-a" / "paper-x.md", title="Paper X", cluster="cluster-a")
    # Sibling notes that reference it
    _write_note_with_links(
        cfg.raw / "cluster-a" / "paper-y.md",
        cluster="cluster-a",
        related=["paper-x", "paper-z"],
    )
    _write_note_with_links(
        cfg.raw / "cluster-a" / "paper-z.md",
        cluster="cluster-a",
        related=["paper-x"],
    )

    result = remove_paper("paper-x")

    assert result["links_cleaned"] == 2
    assert "[[paper-x]]" not in (cfg.raw / "cluster-a" / "paper-y.md").read_text(encoding="utf-8")
    assert "[[paper-x]]" not in (cfg.raw / "cluster-a" / "paper-z.md").read_text(encoding="utf-8")


def test_remove_paper_preserves_other_links_in_siblings(tmp_path, monkeypatch):
    """Only the removed slug is scrubbed; unrelated wikilinks must survive.

    paper-z.md must exist so the v0.84.0 existing_stems safety net
    keeps it in the Related section after paper-x is removed.
    """
    cfg = _make_config(tmp_path, monkeypatch)
    _write_note(cfg.raw / "cluster-a" / "paper-x.md", title="Paper X", cluster="cluster-a")
    _write_note_with_links(
        cfg.raw / "cluster-a" / "paper-y.md",
        cluster="cluster-a",
        related=["paper-x", "paper-z"],
    )
    # Create paper-z so its slug survives the existing_stems filter.
    _write_note(cfg.raw / "cluster-a" / "paper-z.md", title="Paper Z", cluster="cluster-a")

    remove_paper("paper-x")

    text = (cfg.raw / "cluster-a" / "paper-y.md").read_text(encoding="utf-8")
    assert "[[paper-z]]" in text


def test_remove_paper_dry_run_does_not_clean_links(tmp_path, monkeypatch):
    """With dry_run=True nothing is written."""
    cfg = _make_config(tmp_path, monkeypatch)
    _write_note(cfg.raw / "cluster-a" / "paper-x.md", title="Paper X", cluster="cluster-a")
    _write_note_with_links(
        cfg.raw / "cluster-a" / "paper-y.md",
        cluster="cluster-a",
        related=["paper-x"],
    )

    result = remove_paper("paper-x", dry_run=True)

    # dry_run: file not deleted, links not cleaned
    assert (cfg.raw / "cluster-a" / "paper-x.md").exists()
    assert result["links_cleaned"] == 0
    assert "[[paper-x]]" in (cfg.raw / "cluster-a" / "paper-y.md").read_text(encoding="utf-8")
