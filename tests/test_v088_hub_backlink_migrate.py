"""v0.88 #5 — paper notes get a `## Hub` section linking up to overview + MOC."""

from __future__ import annotations

from pathlib import Path

from research_hub.vault.hub_backlink_migrate import (
    HubMigrationResult,
    migrate_all,
    migrate_one_note,
)


def _seed_note(
    path: Path,
    *,
    topic_cluster: str = "human-water-llm",
    add_existing_hub: bool = False,
    add_notes_section: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body_parts = [
        '---',
        'title: "x"',
        f'topic_cluster: "{topic_cluster}"',
        '---',
        '# x',
        '',
        '## Abstract',
        'Abstract content',
        '',
    ]
    if add_existing_hub:
        body_parts.extend([
            '## Hub',
            f'- Cluster: [[{topic_cluster}/00_overview|{topic_cluster}]]',
            '',
        ])
    if add_notes_section:
        body_parts.extend(['## Notes & Annotations', 'note text', ''])
    path.write_text('\n'.join(body_parts), encoding='utf-8')


def test_adds_hub_section_with_cluster_and_mocs(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "human-water-llm" / "p2024-a.md"
    _seed_note(note, topic_cluster="human-water-llm")
    result = migrate_one_note(note)
    assert result.action == "added"
    text = note.read_text(encoding="utf-8")
    assert "## Hub" in text
    assert "[[human-water-llm/00_overview|human-water-llm]]" in text
    assert "[[LLM-Agents]]" in text
    assert "[[Water-Resources]]" in text


def test_hub_section_inserted_before_notes_section(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p1.md"
    _seed_note(note, topic_cluster="demo", add_notes_section=True)
    migrate_one_note(note)
    text = note.read_text(encoding="utf-8")
    hub_pos = text.find("## Hub")
    notes_pos = text.find("## Notes & Annotations")
    assert 0 < hub_pos < notes_pos


def test_idempotent_when_hub_already_present(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p1.md"
    _seed_note(note, topic_cluster="demo", add_existing_hub=True)
    first = note.read_text(encoding="utf-8")
    result = migrate_one_note(note)
    assert result.action == "already_present"
    assert note.read_text(encoding="utf-8") == first


def test_skips_when_no_topic_cluster(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p1.md"
    note.parent.mkdir(parents=True)
    note.write_text('---\ntitle: "x"\n---\n# x\n', encoding="utf-8")
    result = migrate_one_note(note)
    assert result.action == "skipped_no_topic_cluster"


def test_skips_when_no_frontmatter(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p1.md"
    note.parent.mkdir(parents=True)
    note.write_text("# x\nNo frontmatter\n", encoding="utf-8")
    result = migrate_one_note(note)
    assert result.action == "skipped_no_frontmatter"


def test_migrate_all_walks_clusters(tmp_path: Path) -> None:
    _seed_note(tmp_path / "raw" / "human-water-llm" / "p1.md", topic_cluster="human-water-llm")
    _seed_note(tmp_path / "raw" / "human-water-llm" / "p2.md", topic_cluster="human-water-llm", add_existing_hub=True)
    _seed_note(tmp_path / "raw" / "other-cluster" / "p3.md", topic_cluster="other-cluster")
    results = migrate_all(tmp_path)
    assert len(results) == 3
    actions = {r.path.name: r.action for r in results}
    assert actions["p1.md"] == "added"
    assert actions["p2.md"] == "already_present"
    assert actions["p3.md"] == "added"


def test_migrate_all_dry_run_does_not_write(tmp_path: Path) -> None:
    note = tmp_path / "raw" / "demo" / "p1.md"
    _seed_note(note, topic_cluster="demo")
    original = note.read_text(encoding="utf-8")
    results = migrate_all(tmp_path, dry_run=True)
    assert results[0].action == "added"
    assert note.read_text(encoding="utf-8") == original


def test_make_raw_md_emits_hub_section(tmp_path: Path) -> None:
    """Forward-compat: ingest path writes `## Hub` from the start."""
    from research_hub.zotero.fetch import make_raw_md

    item_data = {
        "title": "Test",
        "authors": ["Smith, John"],
        "year": 2024,
        "journal": "X",
        "doi": "10.1/t",
        "abstract": "Long enough abstract content " * 8,
        "tags": [],
        "key": "K",
    }
    rendered = make_raw_md(
        item_data, [], [],
        topic_cluster="demo-cluster",
        moc_links=["LLM-Agents", "Water-Resources"],
    )
    assert "## Hub" in rendered
    assert "[[demo-cluster/00_overview|demo-cluster]]" in rendered
    assert "[[LLM-Agents]]" in rendered
    assert "[[Water-Resources]]" in rendered


def test_make_raw_md_skips_hub_when_no_cluster(tmp_path: Path) -> None:
    """Edge case: bare ingest with no topic_cluster shouldn't emit a Hub section."""
    from research_hub.zotero.fetch import make_raw_md

    item_data = {
        "title": "X", "authors": [], "year": 2024, "journal": "",
        "doi": "", "abstract": "", "tags": [], "key": "K",
    }
    rendered = make_raw_md(item_data, [], [], topic_cluster="")
    assert "## Hub" not in rendered
