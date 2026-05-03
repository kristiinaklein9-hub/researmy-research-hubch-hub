"""v0.78: html-entity decoding for search-backend output before Zotero write."""

from __future__ import annotations

from research_hub.pipeline import _unescape_html_in_paper


def test_decodes_journal_amp():
    pp = {"journal": "AI &amp; SOCIETY", "title": "x"}
    _unescape_html_in_paper(pp)
    assert pp["journal"] == "AI & SOCIETY"


def test_decodes_title_with_lt_gt():
    pp = {"title": "Foo &lt;tag&gt; bar &amp; baz"}
    _unescape_html_in_paper(pp)
    assert pp["title"] == "Foo <tag> bar & baz"


def test_decodes_abstract_field():
    pp = {"abstract": "p &amp; q"}
    _unescape_html_in_paper(pp)
    assert pp["abstract"] == "p & q"


def test_decodes_publication_title_alias():
    pp = {"publicationTitle": "Computers &amp; Education"}
    _unescape_html_in_paper(pp)
    assert pp["publicationTitle"] == "Computers & Education"


def test_decodes_author_name_parts():
    pp = {
        "authors": [
            {"creatorType": "author", "firstName": "M&uuml;ller", "lastName": "Smith"},
            {"creatorType": "author", "name": "Plain &amp; Co"},
            "string-author",  # tolerated, ignored
        ]
    }
    _unescape_html_in_paper(pp)
    assert pp["authors"][0]["firstName"] == "Müller"
    assert pp["authors"][1]["name"] == "Plain & Co"


def test_no_change_when_no_entities():
    pp = {"title": "Plain title", "journal": "JASS", "abstract": "no entities"}
    expected = {k: v for k, v in pp.items()}
    _unescape_html_in_paper(pp)
    assert pp == expected


def test_handles_missing_fields():
    pp = {"slug": "x", "doi": "10.x/y"}
    _unescape_html_in_paper(pp)  # must not raise
    assert pp == {"slug": "x", "doi": "10.x/y"}


def test_handles_non_string_values():
    pp = {"title": None, "journal": 123, "abstract": ["list"]}
    _unescape_html_in_paper(pp)  # must not raise
    assert pp["title"] is None
    assert pp["journal"] == 123
