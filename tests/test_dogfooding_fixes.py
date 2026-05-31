"""Regressions for issues found by dogfooding the live pipeline on a real
topic ("LLM behavioral coherence") on a v1.0.0 install.

1. make_raw_md stamped a hardcoded `ingestion_source="research-hub-v0.3.0"`
   default, so every ingested note was frozen at v0.3.0 regardless of the
   installed version. Now derived from research_hub.__version__.
2. fit_check.emit_prompt, given no cluster definition, fell back to a
   "(no definition supplied for cluster X)" placeholder and then EXTRACTED
   key terms from that placeholder ("definition", "supplied", "cluster"),
   polluting the fit-check prompt. Now: no real definition -> no key terms.
"""

from __future__ import annotations


def test_make_raw_md_ingestion_source_uses_live_version():
    from research_hub import __version__
    from research_hub.zotero.fetch import make_raw_md

    item = {
        "title": "Coherence in LLM Agents",
        "authors": ["Doe, Jane"],
        "year": "2026",
        "journal": "Journal of Testing",
        "doi": "10.1000/coh",
        "abstract": "abc",
        "tags": [],
        "key": "ABCD1234",
    }
    md = make_raw_md(item, ["COLL1"], [], topic_cluster="llm-coherence")

    assert f"research-hub-v{__version__}" in md
    assert "research-hub-v0.3.0" not in md  # the old frozen literal


def test_make_raw_md_explicit_ingestion_source_is_respected():
    # callers like pipeline_repair pass an explicit tag; that must win.
    from research_hub.zotero.fetch import make_raw_md

    item = {
        "title": "X", "authors": ["A"], "year": "2026", "journal": "J",
        "doi": "10.1/x", "abstract": "", "tags": [], "key": "K1",
    }
    md = make_raw_md(item, ["C"], [], ingestion_source="pipeline-repair-v9.9.9")
    assert "pipeline-repair-v9.9.9" in md


def test_fit_check_prompt_no_definition_emits_no_key_terms():
    from research_hub.fit_check import emit_prompt

    prompt = emit_prompt(
        "xyz-fresh-cluster",
        [{"title": "Test", "authors": "A. Author", "doi": "10.1/t", "abstract": "ab"}],
        definition=None,
        cfg=None,
    )
    # the placeholder still appears in the definition block (informational),
    # but the key-terms line must be "none" — NOT the placeholder's own words.
    assert "Key terms: none." in prompt
    assert "Key terms: definition" not in prompt


def test_fit_check_prompt_real_definition_still_extracts_key_terms():
    from research_hub.fit_check import emit_prompt

    prompt = emit_prompt(
        "agents",
        [{"title": "T", "authors": "A", "doi": "10.1/t", "abstract": "ab"}],
        definition="Large language model agents for social simulation and collective behavior.",
        cfg=None,
    )
    assert "Key terms: none." not in prompt
    assert "Key terms:" in prompt
