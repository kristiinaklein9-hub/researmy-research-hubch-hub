"""v0.87.1 #6 — topic:<slug> tag migration."""

from __future__ import annotations

from pathlib import Path

from research_hub.vault.tag_migrate import (
    TagMigrationResult,
    migrate_all,
    migrate_one_note,
)


def _seed_note(
    path: Path,
    *,
    topic_cluster: str | None = "demo-cluster",
    existing_tags: list[str] | None = None,
    add_frontmatter: bool = True,
    add_tags_line: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tags_yaml = ""
    if existing_tags is not None:
        tags_yaml = "[" + ", ".join(f'"{t}"' for t in existing_tags) + "]"
    elif add_tags_line:
        tags_yaml = "[]"

    if not add_frontmatter:
        path.write_text("# Paper\n\nNo frontmatter.\n", encoding="utf-8")
        return

    tc_line = f'topic_cluster: "{topic_cluster}"\n' if topic_cluster else ""
    tags_line = f"tags: {tags_yaml}\n" if add_tags_line else ""
    body = f'---\ntitle: "x"\n{tc_line}{tags_line}---\n# Paper\nbody\n'
    path.write_text(body, encoding="utf-8")


def test_adds_topic_tag_when_missing(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p2024-a.md"
    _seed_note(note, existing_tags=[])
    result = migrate_one_note(note)
    assert result.action == "added"
    assert result.topic_tag == "topic:demo-cluster"
    text = note.read_text(encoding="utf-8")
    assert 'tags: ["topic:demo-cluster"]' in text


def test_preserves_existing_tags(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p2024-a.md"
    _seed_note(note, existing_tags=["research-hub", "type/journalArticle"])
    result = migrate_one_note(note)
    assert result.action == "added"
    text = note.read_text(encoding="utf-8")
    assert 'tags: ["research-hub", "type/journalArticle", "topic:demo-cluster"]' in text


def test_idempotent_when_tag_already_present(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p2024-a.md"
    _seed_note(note, existing_tags=["topic:demo-cluster", "other"])
    result = migrate_one_note(note)
    assert result.action == "already_present"
    # File content untouched
    assert "topic:demo-cluster" in note.read_text(encoding="utf-8")


def test_skips_when_no_topic_cluster(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p2024-a.md"
    _seed_note(note, topic_cluster=None, existing_tags=[])
    result = migrate_one_note(note)
    assert result.action == "skipped_no_topic_cluster"


def test_skips_when_no_frontmatter(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p2024-a.md"
    _seed_note(note, add_frontmatter=False)
    result = migrate_one_note(note)
    assert result.action == "skipped_no_frontmatter"


def test_skips_when_no_tags_line(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p2024-a.md"
    _seed_note(note, add_tags_line=False)
    result = migrate_one_note(note)
    assert result.action == "skipped_no_tags_line"


def test_migrate_all_walks_all_clusters(tmp_path: Path) -> None:
    _seed_note(tmp_path / "raw" / "cluster-a" / "p1.md", topic_cluster="cluster-a", existing_tags=[])
    _seed_note(tmp_path / "raw" / "cluster-a" / "p2.md", topic_cluster="cluster-a", existing_tags=["topic:cluster-a"])
    _seed_note(tmp_path / "raw" / "cluster-b" / "p3.md", topic_cluster="cluster-b", existing_tags=[])
    results = migrate_all(tmp_path)
    assert len(results) == 3
    actions = {r.path.name: r.action for r in results}
    assert actions["p1.md"] == "added"
    assert actions["p2.md"] == "already_present"
    assert actions["p3.md"] == "added"


def test_migrate_all_respects_cluster_filter(tmp_path: Path) -> None:
    _seed_note(tmp_path / "raw" / "cluster-a" / "p1.md", topic_cluster="cluster-a", existing_tags=[])
    _seed_note(tmp_path / "raw" / "cluster-b" / "p2.md", topic_cluster="cluster-b", existing_tags=[])
    results = migrate_all(tmp_path, cluster_slug_filter="cluster-a")
    assert len(results) == 1
    assert results[0].path.name == "p1.md"


def test_migrate_all_dry_run_does_not_write(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p1.md"
    _seed_note(note, topic_cluster="demo", existing_tags=[])
    original = note.read_text(encoding="utf-8")
    results = migrate_all(tmp_path, dry_run=True)
    assert len(results) == 1
    assert results[0].action == "added"
    # but the file is unchanged
    assert note.read_text(encoding="utf-8") == original


def test_make_raw_md_emits_topic_tag(tmp_path: Path) -> None:
    """Forward-compat: new ingest path writes topic:<slug> from the start."""
    from research_hub.zotero.fetch import make_raw_md

    item_data = {
        "title": "Test Paper",
        "authors": ["Smith, John"],
        "year": 2024,
        "journal": "Test Journal",
        "doi": "10.1/test",
        "abstract": "Abstract content",
        "tags": ["research-hub", "type/journalArticle"],
        "key": "TESTKEY01",
    }
    rendered = make_raw_md(item_data, ["COLL01"], [], topic_cluster="my-cluster")
    assert '"topic:my-cluster"' in rendered
    # And the original tags are preserved
    assert '"research-hub"' in rendered
    assert '"type/journalArticle"' in rendered


def test_make_raw_md_does_not_double_emit_topic_tag(tmp_path: Path) -> None:
    """If the caller already put topic:<slug> in tags, don't duplicate it."""
    from research_hub.zotero.fetch import make_raw_md

    item_data = {
        "title": "Test",
        "authors": [],
        "year": 2024,
        "journal": "",
        "doi": "",
        "abstract": "",
        "tags": ["topic:my-cluster", "research-hub"],
        "key": "K",
    }
    rendered = make_raw_md(item_data, [], [], topic_cluster="my-cluster")
    # Exactly one occurrence in the tags array line
    tags_line = [line for line in rendered.splitlines() if line.startswith("tags:")][0]
    assert tags_line.count("topic:my-cluster") == 1
