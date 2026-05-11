"""Tests for research_hub.zotero.fetch helpers."""

from research_hub.zotero.fetch import (
    extract_item_data,
    make_raw_md,
    safe_filename,
    tags_to_wiki_links,
)


def test_safe_filename_basic():
    result = safe_filename("Smith, John", "2024", "A study of flood risk perception")

    assert result == "smith2024-study-flood-risk-perception.md"


def test_safe_filename_handles_missing_year_and_author():
    result = safe_filename("", "", "Untitled draft")

    assert result == "unknownnd-untitled-draft.md"


def test_safe_filename_strips_stopwords():
    result = safe_filename("Doe, Jane", "2024", "The Role of the Agent in a Model")

    assert result == "doe2024-role-agent-model.md"


def test_extract_item_data_skips_attachments():
    assert extract_item_data({"data": {"itemType": "attachment"}}) is None


def test_extract_item_data_basic_fields():
    item = {
        "key": "ABCD1234",
        "data": {
            "itemType": "journalArticle",
            "title": "Flood Risk and Protection Motivation",
            "creators": [
                {"creatorType": "author", "firstName": "Jane", "lastName": "Doe"},
                {"creatorType": "author", "name": "Research Group"},
            ],
            "date": "2024-01-15",
            "publicationTitle": "Risk Journal",
            "DOI": "10.1000/example",
            "url": "https://example.com/paper",
            "abstractNote": "Abstract text",
            "tags": [{"tag": "PMT"}, {"tag": "flood risk"}],
        },
    }

    result = extract_item_data(item)

    assert result == {
        "key": "ABCD1234",
        "item_type": "journalArticle",
        "title": "Flood Risk and Protection Motivation",
        "authors": ["Doe, Jane", "Research Group"],
        "year": "2024",
        "journal": "Risk Journal",
        "doi": "10.1000/example",
        "url": "https://example.com/paper",
        "abstract": "Abstract text",
        "tags": ["PMT", "flood risk"],
    }


def test_tags_to_wiki_links_maps_known_terms():
    """v0.82.0: returns #tag syntax, not [[Wikilink]]. Old wikilink target
    files never existed → produced mega-hub stars in Obsidian graph view.
    """
    links = tags_to_wiki_links(["PMT", "flood risk"])

    assert "#protection-motivation-theory" in links
    assert "#flood-risk" in links


def test_tags_to_wiki_links_empty_for_unknown():
    assert tags_to_wiki_links(["unmapped topic"]) == []


def test_make_raw_md_includes_aliases_and_display_title():
    """v0.83.0: paper notes get aliases + display_title frontmatter so
    Obsidian graph view renders 'Donkers 2025 — ...' instead of dash slug.
    """
    item_data = {
        "key": "ABCD1234",
        "title": "Understanding Online Polarization",
        "authors": ["Donkers, Anna", "Smith, Bob", "Lee, Cara"],
        "year": "2025",
        "journal": "Some Journal",
        "doi": "10.1/example",
        "abstract": "",
        "tags": [],
    }
    md = make_raw_md(item_data, [], [], topic_cluster="test-cluster")

    assert 'aliases: ["Donkers 2025", "Donkers et al. 2025"]' in md
    assert 'display_title: "Donkers 2025 — Understanding Online Polarization"' in md


def test_make_raw_md_single_author_no_et_al():
    item_data = {
        "key": "ABCD",
        "title": "Solo Study",
        "authors": ["Donkers, Anna"],
        "year": "2025",
        "journal": "",
        "doi": "",
        "abstract": "",
        "tags": [],
    }
    md = make_raw_md(item_data, [], [], topic_cluster="x")

    assert 'aliases: ["Donkers 2025"]' in md
    assert "et al." not in md.split("---\n", 2)[1]  # not in frontmatter
    assert 'display_title: "Donkers 2025 — Solo Study"' in md


def test_make_raw_md_placeholder_author_falls_back_to_empty_aliases():
    """When Zotero ingestion has placeholder authors like '(See arXiv ...)',
    aliases is empty and display_title is also empty (avoid garbage display).
    """
    item_data = {
        "key": "ABCD",
        "title": "(See arXiv 2505.07087)",
        "authors": ["(See arXiv 2505.07087)"],
        "year": "2025",
        "journal": "",
        "doi": "",
        "abstract": "",
        "tags": [],
    }
    md = make_raw_md(item_data, [], [], topic_cluster="x")

    assert "aliases: []" in md
    assert 'display_title: ""' in md


def test_tags_to_wiki_links_never_emits_wikilink_brackets():
    """Regression test for the 7 GB graph-pollution bug (2026-05-11).

    Prior to v0.82.0, every paper with an 'llm' tag got `[[LLM-Agents]]`
    injected — an unresolved wikilink — creating a 115-edge mega-star hub
    in Obsidian's graph view. Ensure no value in TAG_WIKI_MAP regresses.
    """
    sample_tags = [
        "llm", "memory", "abm", "flood", "social capital", "metacognition",
        "multi-agent", "place attachment", "natural language",
    ]
    links = tags_to_wiki_links(sample_tags)
    assert links, "expected at least one concept tag"
    for link in links:
        assert "[[" not in link and "]]" not in link, (
            f"v0.82.0 regression: TAG_WIKI_MAP must emit #tag not [[Wikilink]]; got {link!r}"
        )
        assert link.startswith("#"), f"expected #tag prefix, got {link!r}"


def test_make_raw_md_contains_yaml_frontmatter():
    item_data = {
        "key": "ABCD1234",
        "title": "Flood Risk and Protection Motivation",
        "authors": ["Doe, Jane"],
        "year": "2024",
        "journal": "Risk Journal",
        "doi": "10.1000/example",
        "abstract": "Abstract text",
        "tags": ["PMT", "flood risk"],
    }

    markdown = make_raw_md(item_data, ["Survey Papers"], ["Important note"])

    assert markdown.startswith('---\ntitle: "Flood Risk and Protection Motivation"')
    assert "zotero-key: ABCD1234" in markdown
    assert "\n## Abstract\n\nAbstract text\n" in markdown
