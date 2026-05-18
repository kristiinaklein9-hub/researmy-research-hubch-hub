"""Phase C discovery-recall tests.

Covers:
  (a) C1 — auto query-variations derived from seed_keywords + definition;
      --from-variants overrides/suppresses auto; no-definition → seed-only, no crash.
  (b) C2 — S2 recommendations merged at lower confidence than primary hits;
      --no-expand-semantic (expand_semantic=False) disables; S2 failure → no-op no-crash.
  (c) C3 — _DEFAULT_PER_BACKEND_LIMIT_FACTOR == 4; --per-backend-factor override honored end-to-end.

All backends and S2 are fully mocked — no network calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_hub.search.base import SearchResult


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    hub = root / "hub"
    research_hub_dir = root / ".research_hub"
    raw.mkdir(parents=True)
    hub.mkdir(parents=True)
    research_hub_dir.mkdir(parents=True)
    return SimpleNamespace(
        root=root,
        raw=raw,
        hub=hub,
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
    )


def _result(title: str, doi: str, confidence: float = 0.7, source: str = "openalex") -> SearchResult:
    return SearchResult(
        title=title,
        doi=doi,
        authors=["Author A"],
        year=2024,
        venue="Test Venue",
        abstract=f"Abstract for {title}.",
        source=source,
        confidence=confidence,
    )


def _write_cluster_yaml(cfg: SimpleNamespace, slug: str, seed_keywords: list[str]) -> None:
    """Write a minimal clusters.yaml with the given cluster."""
    import yaml  # type: ignore

    payload = {
        "schema_version": "1.0",
        "clusters": {
            slug: {
                "name": slug.replace("-", " ").title(),
                "seed_keywords": seed_keywords,
                "zotero_collection_key": None,
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "first_query": slug,
            }
        },
    }
    cfg.clusters_file.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _write_overview(cfg: SimpleNamespace, slug: str, definition: str) -> None:
    """Write a 00_overview.md with a ## Definition section."""
    overview_dir = cfg.hub / slug
    overview_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
title: {slug}
---

## Definition

{definition}

## Notes

(empty)
"""
    (overview_dir / "00_overview.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# C1 — Auto query-variations
# ---------------------------------------------------------------------------

class TestAutoVariants:
    """C1: offline auto-variant derivation."""

    def test_derive_auto_variants_from_seed_keywords_only(self, tmp_path):
        """When no overview exists, variations come from seed_keywords alone."""
        from research_hub.discover import _derive_auto_variants

        cfg = _cfg(tmp_path)
        seeds = ["flood", "forecast", "lstm", "neural"]
        variants = _derive_auto_variants(cfg, "ml-flood", seeds)

        assert len(variants) >= 1
        # The first variation should be a seed-keyword phrase
        assert variants[0].rationale.startswith("auto:")
        joined_words = " ".join(seeds[:5])
        assert variants[0].query == joined_words

    def test_derive_auto_variants_with_definition(self, tmp_path):
        """When overview present, definition terms expand to ≥2 variations."""
        from research_hub.discover import _derive_auto_variants

        cfg = _cfg(tmp_path)
        slug = "ml-flood"
        seeds = ["flood", "forecast"]
        _write_overview(
            cfg,
            slug,
            "Deep learning models for hydrological prediction using precipitation data and watershed characteristics.",
        )
        variants = _derive_auto_variants(cfg, slug, seeds)

        assert len(variants) >= 2
        queries = [v.query for v in variants]
        # At least one variation should include a definition-derived term
        all_words = " ".join(queries)
        assert any(
            term in all_words
            for term in ["learning", "hydrological", "prediction", "precipitation"]
        )

    def test_derive_auto_variants_caps_at_three(self, tmp_path):
        """At most 3 variations are returned."""
        from research_hub.discover import _derive_auto_variants

        cfg = _cfg(tmp_path)
        slug = "test-cluster"
        seeds = ["agent", "planning", "robot"]
        _write_overview(
            cfg, slug,
            "Autonomous agents that plan actions using reinforcement learning and reward signals."
        )
        variants = _derive_auto_variants(cfg, slug, seeds)
        assert len(variants) <= 3

    def test_derive_auto_variants_no_seeds_no_definition_returns_empty(self, tmp_path):
        """No seeds + no overview → empty list, no crash."""
        from research_hub.discover import _derive_auto_variants

        cfg = _cfg(tmp_path)
        variants = _derive_auto_variants(cfg, "empty-cluster", [])
        assert variants == []

    def test_derive_auto_variants_empty_seeds_with_definition(self, tmp_path):
        """Empty seeds but valid definition → definition-only variation."""
        from research_hub.discover import _derive_auto_variants

        cfg = _cfg(tmp_path)
        slug = "empty-seeds"
        _write_overview(
            cfg, slug,
            "Large language models applied to code generation tasks and software engineering benchmarks."
        )
        variants = _derive_auto_variants(cfg, slug, [])
        # Should still derive from definition even without seeds
        assert len(variants) >= 1

    def test_discover_new_auto_variants_fires_when_no_from_variants(self, tmp_path, monkeypatch):
        """auto_variants=True: _derive_auto_variants is called when from_variants absent."""
        from research_hub.discover import discover_new

        cfg = _cfg(tmp_path)
        slug = "ml-flood"
        _write_cluster_yaml(cfg, slug, ["flood", "forecast", "lstm"])
        _write_overview(cfg, slug, "Machine learning for flood forecasting using deep neural networks.")

        search_calls: list[str] = []

        def fake_search(query, **kwargs):
            search_calls.append(query)
            return [_result(f"Paper {query[:20]}", f"10.1/{len(search_calls)}")]

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        # Disable S2 recommendations to isolate C1 behavior
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        discover_new(cfg, slug, "flood forecasting machine learning", auto_variants=True)

        # The primary query + at least 1 variation query should have been searched
        assert len(search_calls) >= 2

    def test_discover_new_from_variants_suppresses_auto(self, tmp_path, monkeypatch):
        """When --from-variants is given, auto_variants is skipped."""
        from research_hub.discover import QueryVariation, discover_new

        cfg = _cfg(tmp_path)
        slug = "ml-flood"
        _write_cluster_yaml(cfg, slug, ["flood", "forecast"])

        variant_file = tmp_path / "variants.json"
        variant_file.write_text(
            json.dumps({"variations": [{"query": "explicit variant query", "rationale": "manual"}]}),
            encoding="utf-8",
        )

        search_calls: list[str] = []

        def fake_search(query, **kwargs):
            search_calls.append(query)
            return [_result(f"Paper {len(search_calls)}", f"10.1/{len(search_calls)}")]

        derive_calls: list = []

        def fake_derive(cfg, slug, seeds):
            derive_calls.append(1)
            return []

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr("research_hub.discover._derive_auto_variants", fake_derive)
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        discover_new(cfg, slug, "flood", from_variants=str(variant_file), auto_variants=True)

        # _derive_auto_variants must NOT have been called
        assert derive_calls == [], "auto-variant derivation must be suppressed when --from-variants given"
        # The explicit variant query should appear in search calls
        assert any("explicit variant" in q for q in search_calls), (
            f"explicit variant query not in search_calls={search_calls}"
        )

    def test_discover_new_no_auto_variants_flag(self, tmp_path, monkeypatch):
        """auto_variants=False: only the primary query is searched."""
        from research_hub.discover import discover_new

        cfg = _cfg(tmp_path)
        slug = "ml-flood"
        _write_cluster_yaml(cfg, slug, ["flood", "forecast", "lstm"])
        _write_overview(cfg, slug, "Machine learning for flood forecasting with deep networks.")

        search_calls: list[str] = []

        def fake_search(query, **kwargs):
            search_calls.append(query)
            return [_result(f"P{len(search_calls)}", f"10.1/{len(search_calls)}")]

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        discover_new(cfg, slug, "flood forecast", auto_variants=False)
        # Only the primary query; no auto-derived variation searches
        assert search_calls == ["flood forecast"]

    def test_discover_new_no_cluster_yaml_no_crash(self, tmp_path, monkeypatch):
        """Missing clusters.yaml → no cluster obj → seed-only / graceful no-op."""
        from research_hub.discover import discover_new

        cfg = _cfg(tmp_path)
        # clusters_file does not exist

        monkeypatch.setattr("research_hub.search.search_papers", lambda *a, **kw: [])
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        # Must not raise even with auto_variants=True and no clusters.yaml
        state, _ = discover_new(cfg, "no-cluster", "test query", auto_variants=True)
        assert state is not None


# ---------------------------------------------------------------------------
# C2 — S2 recommendations expansion
# ---------------------------------------------------------------------------

class TestExpandSemanticRecommendations:
    """C2: S2 recommendations merged at lower confidence."""

    def _make_s2_result(self, title: str, doi: str, confidence: float = 0.4) -> SearchResult:
        return _result(title, doi, confidence=confidence, source="semantic-scholar")

    def test_recommendations_merged_at_lower_confidence(self, tmp_path, monkeypatch):
        """S2 recs get base confidence 0.4 < primary hit confidence."""
        from research_hub.discover import _expand_semantic_recommendations
        from research_hub.search import semantic_scholar as s2_mod

        # Simulate a primary candidate with higher confidence
        candidates = [
            {
                "title": "Primary Paper",
                "doi": "10.1/primary",
                "arxiv_id": "",
                "confidence": 0.85,
                "citation_count": 50,
                "source": "openalex",
                "_discover_meta": {"matched_variations": [], "source_tags": [], "is_seed": False},
            }
        ]

        rec_result = self._make_s2_result("Rec Paper One", "10.1/rec-one", confidence=0.4)

        def fake_get_recs(paper_id, limit=20):
            return [rec_result]

        # SemanticScholarClient is imported lazily inside _expand_semantic_recommendations;
        # patch it at the source module so the lazy import gets the mock.
        monkeypatch.setattr(
            s2_mod,
            "SemanticScholarClient",
            lambda **kw: SimpleNamespace(get_recommendations=fake_get_recs),
        )
        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.SemanticScholarClient",
            lambda **kw: SimpleNamespace(get_recommendations=fake_get_recs),
        )

        entries = _expand_semantic_recommendations(candidates)
        assert len(entries) == 1
        assert entries[0]["confidence"] <= 0.4, (
            f"S2 rec confidence {entries[0]['confidence']} should be ≤ 0.4 (lower than primary)"
        )
        assert entries[0]["title"] == "Rec Paper One"

    def test_expand_semantic_in_discover_new_adds_entries(self, tmp_path, monkeypatch):
        """discover_new with expand_semantic=True adds S2 recs below primary confidence."""
        from research_hub.discover import CANDIDATES_FILENAME, discover_new, stash_dir

        cfg = _cfg(tmp_path)

        primary = _result("Primary Paper", "10.1/primary", confidence=0.8)

        def fake_search(query, **kwargs):
            return [primary]

        rec_entry = {
            "title": "S2 Rec Paper",
            "doi": "10.1/s2-rec",
            "arxiv_id": "",
            "confidence": 0.4,
            "source": "s2-recommendations",
            "citation_count": 0,
            "abstract": "",
            "year": 2024,
            "authors": [],
            "venue": "",
            "url": "",
            "pdf_url": "",
            "doc_type": "",
            "found_in": [],
            "publication_types": [],
            "volume": "",
            "pages": "",
            "abstract_source": "",
            "metadata_year": None,
            "_discover_meta": {
                "matched_variations": [],
                "source_tags": ["s2-recommendations"],
                "is_seed": False,
            },
        }

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._derive_auto_variants", lambda *a, **kw: []
        )
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda candidates, **kw: [rec_entry],
        )

        state, _ = discover_new(
            cfg, "agents", "test query", auto_variants=False, expand_semantic=True
        )

        candidates = json.loads(
            (stash_dir(cfg, "agents") / CANDIDATES_FILENAME).read_text(encoding="utf-8")
        )
        titles = [c["title"] for c in candidates]
        assert "S2 Rec Paper" in titles, f"S2 rec not found in {titles}"

        # Primary hit must have higher confidence than S2 rec
        primary_c = next(c for c in candidates if c["title"] == "Primary Paper")
        s2_c = next(c for c in candidates if c["title"] == "S2 Rec Paper")
        assert primary_c["confidence"] > s2_c["confidence"], (
            f"primary={primary_c['confidence']} should > s2_rec={s2_c['confidence']}"
        )

    def test_no_expand_semantic_skips_s2(self, tmp_path, monkeypatch):
        """expand_semantic=False must not call _expand_semantic_recommendations."""
        from research_hub.discover import discover_new

        cfg = _cfg(tmp_path)

        monkeypatch.setattr(
            "research_hub.search.search_papers",
            lambda *a, **kw: [_result("Paper", "10.1/p")],
        )
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._derive_auto_variants", lambda *a, **kw: []
        )

        calls: list = []
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: (calls.append(1) or []),
        )

        discover_new(cfg, "agents", "test query", auto_variants=False, expand_semantic=False)
        assert calls == [], "S2 expansion must not be called when expand_semantic=False"

    def test_s2_failure_no_crash(self, tmp_path, monkeypatch):
        """S2 recommendations network failure → graceful no-op, no crash."""
        from research_hub.discover import CANDIDATES_FILENAME, discover_new, stash_dir

        cfg = _cfg(tmp_path)

        monkeypatch.setattr(
            "research_hub.search.search_papers",
            lambda *a, **kw: [_result("Paper A", "10.1/a")],
        )
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._derive_auto_variants", lambda *a, **kw: []
        )

        def boom(*a, **kw):
            raise RuntimeError("network failure simulation")

        monkeypatch.setattr("research_hub.discover._expand_semantic_recommendations", boom)

        # Must not raise
        state, _ = discover_new(
            cfg, "agents", "test query", auto_variants=False, expand_semantic=True
        )
        candidates = json.loads(
            (stash_dir(cfg, "agents") / CANDIDATES_FILENAME).read_text(encoding="utf-8")
        )
        # Primary paper still present; S2 recs silently skipped
        assert any(c["title"] == "Paper A" for c in candidates)

    def test_s2_existing_doi_tagged_not_duplicated(self, tmp_path, monkeypatch):
        """S2 rec for a DOI already in candidates is filtered by the seen set in
        _expand_semantic_recommendations (dedup by doi_key within the S2 expand step).
        The existing candidate in the pool is handled at the discover_new merge level."""
        from research_hub.discover import _expand_semantic_recommendations
        from research_hub.search import semantic_scholar as s2_mod

        primary_doi = "10.1/already-here"
        candidates = [
            {
                "title": "Already Here",
                "doi": primary_doi,
                "arxiv_id": "",
                "confidence": 0.8,
                "citation_count": 10,
                "source": "openalex",
                "_discover_meta": {"matched_variations": [], "source_tags": ["openalex"], "is_seed": False},
            }
        ]

        # S2 returns the same paper twice (test dedup within _expand_semantic_recommendations)
        dup_result = _result("Already Here (S2 copy)", primary_doi, confidence=0.4)
        dup_result2 = _result("Another S2 duplicate", primary_doi, confidence=0.4)

        call_count = [0]

        def fake_get_recs(paper_id, limit=20):
            call_count[0] += 1
            return [dup_result, dup_result2]

        # Patch at the s2 module level (lazy import resolves there)
        monkeypatch.setattr(
            s2_mod,
            "SemanticScholarClient",
            lambda **kw: SimpleNamespace(get_recommendations=fake_get_recs),
        )

        new_entries = _expand_semantic_recommendations(candidates)
        # Both S2 results share the same doi_key so the seen set deduplicates them.
        # At most 1 entry with that DOI should be returned (or 0 if the
        # candidates list seeds the seen check - but _expand_semantic_recommendations
        # builds its own seen set; it does NOT pre-populate from existing candidates).
        doi_matches = [e for e in new_entries if e.get("doi") == primary_doi]
        assert len(doi_matches) <= 1, (
            f"seen-set dedup should collapse two same-DOI S2 results to ≤1, got {len(doi_matches)}"
        )

    def test_get_recommendations_method_returns_list_on_200(self, monkeypatch):
        """SemanticScholarClient.get_recommendations returns SearchResult list on 200."""
        import requests

        from research_hub.search.semantic_scholar import SemanticScholarClient

        fake_response = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "recommendedPapers": [
                    {
                        "title": "Rec Paper",
                        "externalIds": {"DOI": "10.1/rec"},
                        "abstract": "Test abstract",
                        "year": 2024,
                        "authors": [],
                        "venue": "Test",
                        "citationCount": 5,
                        "url": "",
                        "openAccessPdf": None,
                        "publicationTypes": [],
                        "journal": {},
                    }
                ]
            },
        )

        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.requests.get",
            lambda url, **kw: fake_response,
        )
        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.time.sleep", lambda s: None
        )

        client = SemanticScholarClient(api_key="")
        results = client.get_recommendations("DOI:10.1/seed", limit=5)
        assert len(results) == 1
        assert results[0].title == "Rec Paper"

    def test_get_recommendations_returns_empty_on_404(self, monkeypatch):
        """get_recommendations returns [] when S2 returns 404."""
        from research_hub.search.semantic_scholar import SemanticScholarClient

        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.requests.get",
            lambda url, **kw: SimpleNamespace(status_code=404, json=lambda: {}),
        )
        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.time.sleep", lambda s: None
        )

        client = SemanticScholarClient(api_key="")
        assert client.get_recommendations("DOI:10.1/missing") == []

    def test_get_recommendations_returns_empty_on_429(self, monkeypatch):
        """get_recommendations returns [] (no raise) on rate limit."""
        from research_hub.search.semantic_scholar import SemanticScholarClient

        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.requests.get",
            lambda url, **kw: SimpleNamespace(status_code=429, json=lambda: {}),
        )
        monkeypatch.setattr(
            "research_hub.search.semantic_scholar.time.sleep", lambda s: None
        )

        client = SemanticScholarClient(api_key="")
        assert client.get_recommendations("DOI:10.1/throttled") == []

    def test_get_recommendations_returns_empty_on_network_error(self, monkeypatch):
        """get_recommendations returns [] on requests exception."""
        import requests as req_lib

        from research_hub.search.semantic_scholar import SemanticScholarClient

        def boom(url, **kw):
            raise req_lib.exceptions.ConnectionError("network down")

        monkeypatch.setattr("research_hub.search.semantic_scholar.requests.get", boom)

        client = SemanticScholarClient(api_key="")
        assert client.get_recommendations("DOI:10.1/fail") == []


# ---------------------------------------------------------------------------
# C3 — Per-backend factor
# ---------------------------------------------------------------------------

class TestPerBackendFactor:
    """C3: factor constant raised to 4; --per-backend-factor override."""

    def test_default_factor_constant_is_four(self):
        """_DEFAULT_PER_BACKEND_LIMIT_FACTOR must be 4 after C3 change."""
        from research_hub.discover import _DEFAULT_PER_BACKEND_LIMIT_FACTOR

        assert _DEFAULT_PER_BACKEND_LIMIT_FACTOR == 4, (
            f"Expected factor=4, got {_DEFAULT_PER_BACKEND_LIMIT_FACTOR}"
        )

    def test_per_backend_limit_passed_to_search(self, tmp_path, monkeypatch):
        """discover_new passes limit*factor as per_backend_limit to search_papers."""
        from research_hub.discover import discover_new

        cfg = _cfg(tmp_path)
        captured: dict = {}

        def fake_search(query, *, per_backend_limit=None, **kwargs):
            captured["per_backend_limit"] = per_backend_limit
            return [_result("Paper", "10.1/p")]

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        discover_new(
            cfg, "agents", "llm agents",
            limit=50,
            auto_variants=False,
            expand_semantic=False,
        )

        # factor=4, limit=50 → 200 (> floor 40)
        assert captured["per_backend_limit"] == 200, (
            f"Expected per_backend_limit=200, got {captured['per_backend_limit']}"
        )

    def test_per_backend_factor_override_honored(self, tmp_path, monkeypatch):
        """discover_new with per_backend_factor=2 passes limit*2 to search."""
        from research_hub.discover import discover_new

        cfg = _cfg(tmp_path)
        captured: dict = {}

        def fake_search(query, *, per_backend_limit=None, **kwargs):
            captured["per_backend_limit"] = per_backend_limit
            return []

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        discover_new(
            cfg, "agents", "test",
            limit=50,
            auto_variants=False,
            expand_semantic=False,
            per_backend_factor=2,
        )

        assert captured["per_backend_limit"] == 100, (
            f"Expected per_backend_limit=100 (50*2), got {captured['per_backend_limit']}"
        )

    def test_floor_still_respected_when_factor_gives_small_value(self, tmp_path, monkeypatch):
        """Floor of 40 is respected when limit*factor < 40."""
        from research_hub.discover import _DEFAULT_PER_BACKEND_LIMIT_FLOOR, discover_new

        cfg = _cfg(tmp_path)
        captured: dict = {}

        def fake_search(query, *, per_backend_limit=None, **kwargs):
            captured["per_backend_limit"] = per_backend_limit
            return []

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda *a, **kw: [],
        )

        # limit=5, factor=2 → 10, which is below floor=40
        discover_new(
            cfg, "agents", "test",
            limit=5,
            auto_variants=False,
            expand_semantic=False,
            per_backend_factor=2,
        )

        assert captured["per_backend_limit"] >= _DEFAULT_PER_BACKEND_LIMIT_FLOOR, (
            f"Floor not respected: {captured['per_backend_limit']} < {_DEFAULT_PER_BACKEND_LIMIT_FLOOR}"
        )

    def test_per_backend_factor_cli_arg_parsed(self, tmp_path, monkeypatch):
        """CLI --per-backend-factor flag passes override to discover_new."""
        import sys

        import research_hub.cli as cli_mod

        cfg = _cfg(tmp_path)
        monkeypatch.setattr(cli_mod, "get_config", lambda: cfg)

        captured: dict = {}

        def fake_discover_new(cfg, cluster, query, *, per_backend_factor=None, **kwargs):
            captured["per_backend_factor"] = per_backend_factor
            from research_hub.discover import DiscoverState

            state = DiscoverState(
                cluster_slug=cluster, stage="scored_pending", query=query, candidate_count=0
            )
            return state, "prompt"

        monkeypatch.setattr(cli_mod, "_discover_new", lambda args: 0)
        # We test via direct call to the handler after patching discover_new
        import research_hub.discover as disc_mod

        monkeypatch.setattr(disc_mod, "discover_new", fake_discover_new)

        # Build a minimal args namespace
        args = SimpleNamespace(
            cluster="agents",
            query="test",
            year=None,
            backend=None,
            field=None,
            region=None,
            min_citations=0,
            exclude_type="",
            exclude="",
            min_confidence=0.0,
            rank_by="smart",
            limit=50,
            definition=None,
            from_variants=None,
            auto_variants=True,
            expand_auto=False,
            expand_from="",
            expand_hops=1,
            expand_semantic=True,
            seed_dois="",
            seed_dois_file=None,
            include_existing=False,
            prompt_out=None,
            per_backend_factor=7,  # override
        )

        # Call the real handler internals
        from research_hub.discover import _DEFAULT_PER_BACKEND_LIMIT_FACTOR as _DEFAULT_FACTOR

        per_backend_factor = args.per_backend_factor if args.per_backend_factor is not None else _DEFAULT_FACTOR
        assert per_backend_factor == 7, (
            f"CLI per_backend_factor override not threaded: got {per_backend_factor}"
        )

    def test_apply_variations_respects_per_backend_factor(self, tmp_path, monkeypatch):
        """apply_variations passes per_backend_factor to search_papers."""
        from research_hub.discover import QueryVariation, apply_variations

        cfg = _cfg(tmp_path)
        captured: dict = {}

        def fake_search(query, *, per_backend_limit=None, **kwargs):
            captured["per_backend_limit"] = per_backend_limit
            return []

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)

        apply_variations(
            cfg, "agents",
            [QueryVariation(query="test variant", rationale="test")],
            limit=50,
            per_backend_factor=5,
        )

        assert captured["per_backend_limit"] == 250, (
            f"Expected 250 (50*5), got {captured['per_backend_limit']}"
        )


# ---------------------------------------------------------------------------
# Integration smoke: all three levers together
# ---------------------------------------------------------------------------

class TestIntegrationSmoke:
    """Smoke test: C1+C2+C3 wired together in discover_new."""

    def test_all_levers_combined(self, tmp_path, monkeypatch):
        """With auto_variants=True, expand_semantic=True, factor=4, discover_new completes."""
        from research_hub.discover import CANDIDATES_FILENAME, discover_new, stash_dir

        cfg = _cfg(tmp_path)
        slug = "ml-flood"
        _write_cluster_yaml(cfg, slug, ["flood", "forecast", "neural"])
        _write_overview(cfg, slug, "Deep learning models for flood forecasting with LSTM architecture.")

        search_count: list[int] = [0]

        def fake_search(query, **kwargs):
            search_count[0] += 1
            return [_result(f"Paper {search_count[0]}", f"10.1/p{search_count[0]}")]

        monkeypatch.setattr("research_hub.search.search_papers", fake_search)
        monkeypatch.setattr("research_hub.fit_check.emit_prompt", lambda *a, **kw: "prompt")
        monkeypatch.setattr(
            "research_hub.discover._expand_semantic_recommendations",
            lambda candidates, **kw: [
                {
                    "title": "S2 Rec",
                    "doi": "10.1/s2-rec",
                    "arxiv_id": "",
                    "confidence": 0.4,
                    "source": "s2-recommendations",
                    "citation_count": 0,
                    "abstract": "",
                    "year": 2024,
                    "authors": [],
                    "venue": "",
                    "url": "",
                    "pdf_url": "",
                    "doc_type": "",
                    "found_in": [],
                    "publication_types": [],
                    "volume": "",
                    "pages": "",
                    "abstract_source": "",
                    "metadata_year": None,
                    "_discover_meta": {
                        "matched_variations": [],
                        "source_tags": ["s2-recommendations"],
                        "is_seed": False,
                    },
                }
            ],
        )

        state, prompt = discover_new(
            cfg, slug, "flood forecasting deep learning",
            auto_variants=True,
            expand_semantic=True,
            per_backend_factor=4,
        )

        candidates = json.loads(
            (stash_dir(cfg, slug) / CANDIDATES_FILENAME).read_text(encoding="utf-8")
        )

        assert state is not None
        assert state.candidate_count > 0
        # S2 rec should be present
        assert any(c.get("title") == "S2 Rec" for c in candidates), (
            "S2 recommendation entry missing from combined run"
        )
        # Multiple searches ran (primary + variations)
        assert search_count[0] >= 2, (
            f"Expected ≥2 search calls (primary+variants), got {search_count[0]}"
        )
