from __future__ import annotations

from pathlib import Path

from research_hub.vault.link_updater import (
    NoteMeta,
    add_wikilinks_to_note,
    find_related_in_cluster,
    parse_frontmatter,
)


def test_parse_frontmatter_extracts_title_tags_cluster(tmp_path):
    note = tmp_path / "paper.md"
    note.write_text(
        '---\n'
        'title: "Paper"\n'
        'tags: ["llm", "agents"]\n'
        'topic_cluster: "cluster-a"\n'
        '---\n',
        encoding="utf-8",
    )

    meta = parse_frontmatter(note)

    assert meta is not None
    assert meta.title == "Paper"
    assert meta.tags == ["llm", "agents"]
    assert meta.topic_cluster == "cluster-a"


def test_parse_frontmatter_returns_none_for_no_yaml(tmp_path):
    note = tmp_path / "paper.md"
    note.write_text("# No YAML", encoding="utf-8")

    assert parse_frontmatter(note) is None


def test_find_related_in_cluster_filters_by_cluster():
    new_note = NoteMeta(Path("new.md"), "New", ["llm"], "cluster-a")
    notes = [
        NoteMeta(Path("one.md"), "One", ["llm"], "cluster-a"),
        NoteMeta(Path("two.md"), "Two", ["llm"], "cluster-b"),
    ]

    related = find_related_in_cluster(new_note, notes)

    assert [item.slug for item in related] == ["one"]


def test_find_related_in_cluster_ranks_by_tag_overlap():
    new_note = NoteMeta(Path("new.md"), "New", ["llm", "agents"], "cluster-a")
    notes = [
        NoteMeta(Path("one.md"), "One", ["llm"], "cluster-a"),
        NoteMeta(Path("two.md"), "Two", ["llm", "agents"], "cluster-a"),
    ]

    related = find_related_in_cluster(new_note, notes)

    assert [item.slug for item in related] == ["two", "one"]


def test_add_wikilinks_to_note_creates_section(tmp_path):
    note = tmp_path / "paper.md"
    note.write_text("---\ntitle: \"Paper\"\n---\n", encoding="utf-8")

    changed = add_wikilinks_to_note(note, ["other-paper"])

    assert changed is True
    assert "## Related Papers in This Cluster" in note.read_text(encoding="utf-8")


def test_add_wikilinks_to_note_idempotent_update(tmp_path):
    note = tmp_path / "paper.md"
    note.write_text("---\ntitle: \"Paper\"\n---\n", encoding="utf-8")

    add_wikilinks_to_note(note, ["other-paper"])
    changed = add_wikilinks_to_note(note, ["other-paper"])

    assert changed is False


def test_add_wikilinks_filters_nonexistent_slugs(tmp_path):
    """v0.84.0 regression test: when existing_stems is provided, broken
    wikilinks (target file doesn't exist) must not be written. This is
    the safety net against the 2026-05-11 graph-hygiene incident where
    1,199 broken cross-refs were left in the vault after historical slug
    formula divergence between safe_filename and slugify(title)[:60].
    """
    note = tmp_path / "paper.md"
    note.write_text("---\ntitle: \"Paper\"\n---\n", encoding="utf-8")

    # Mix of real (exist in vault) and broken (don't exist) slugs
    existing = {"real-paper-2024", "another-real-2025"}
    add_wikilinks_to_note(
        note,
        ["real-paper-2024", "broken-phantom-2023", "another-real-2025", "ghost-paper-2022"],
        existing_stems=existing,
    )

    content = note.read_text(encoding="utf-8")
    assert "[[real-paper-2024]]" in content
    assert "[[another-real-2025]]" in content
    assert "[[broken-phantom-2023]]" not in content
    assert "[[ghost-paper-2022]]" not in content


def test_add_wikilinks_without_existing_stems_writes_all(tmp_path):
    """Backward compat: when existing_stems is None, write all slugs
    (legacy behavior preserved for callers that don't yet pass the set).
    """
    note = tmp_path / "paper.md"
    note.write_text("---\ntitle: \"Paper\"\n---\n", encoding="utf-8")

    add_wikilinks_to_note(note, ["slug-a", "slug-b"], existing_stems=None)

    content = note.read_text(encoding="utf-8")
    assert "[[slug-a]]" in content
    assert "[[slug-b]]" in content
