from __future__ import annotations

import pytest

from research_hub.search.fallback import FIELD_PRESETS, _BACKEND_REGISTRY, resolve_backends_for_field


def test_resolve_field_cs_contains_core_backends():
    """cs preset must include the key academic-CS backends (exact order/count may grow)."""
    backends = resolve_backends_for_field("cs")
    for expected in ("openalex", "arxiv", "semantic-scholar", "dblp", "crossref", "google-scholar"):
        assert expected in backends, f"cs preset missing {expected!r}"


def test_resolve_field_bio_includes_pubmed_and_biorxiv():
    resolved = resolve_backends_for_field("bio")
    assert "pubmed" in resolved
    assert "biorxiv" in resolved


def test_resolve_field_med_includes_pubmed():
    assert "pubmed" in resolve_backends_for_field("med")


def test_resolve_field_social_includes_repec():
    assert "repec" in resolve_backends_for_field("social")


def test_resolve_field_econ_includes_repec():
    assert "repec" in resolve_backends_for_field("econ")


def test_resolve_field_chem_includes_chemrxiv():
    assert "chemrxiv" in resolve_backends_for_field("chem")


def test_resolve_field_astro_includes_nasa_ads():
    assert "nasa-ads" in resolve_backends_for_field("astro")


def test_resolve_field_edu_includes_eric():
    assert "eric" in resolve_backends_for_field("edu")


def test_resolve_field_general_contains_all_core_backends():
    """general preset must include every domain backend (exact count may grow with new backends)."""
    backends = resolve_backends_for_field("general")
    for expected in (
        "openalex", "arxiv", "semantic-scholar", "crossref", "dblp",
        "pubmed", "biorxiv", "repec", "ssrn", "chemrxiv", "nasa-ads",
        "eric", "google-scholar",
    ):
        assert expected in backends, f"general preset missing {expected!r}"
    # general must be the superset — at least as many as this wave's 13
    assert len(backends) >= 13, f"general preset shrank below 13 backends (got {len(backends)})"


def test_resolve_field_unknown_raises_valueerror_with_valid_list():
    with pytest.raises(
        ValueError,
        match="valid: astro, bio, chem, cs, econ, edu, general, math, med, physics, social",
    ):
        resolve_backends_for_field("unknown")


def test_field_presets_constants_match_registered_backends():
    preset_backends = {backend for backends in FIELD_PRESETS.values() for backend in backends}
    assert preset_backends <= set(_BACKEND_REGISTRY.keys())
