"""v0.88 #10 — mark-kept --list --show-counts / --by-pattern enrich the
opaque 8-char Zotero key list with collection name + item count."""

from __future__ import annotations

from types import SimpleNamespace

from research_hub.zotero.gc import lookup_collection_names_and_counts


def _stub_zot(collections: list[dict]):
    """Build a minimal pyzotero stub that paginates the collections list."""
    chunks_yielded = {"n": 0}

    def collections_call(limit=200, start=0):
        if chunks_yielded["n"] > 0:
            return []
        chunks_yielded["n"] += 1
        return collections

    return SimpleNamespace(collections=collections_call)


def _make_collection(key: str, name: str, items: int = 0, subs: int = 0) -> dict:
    return {
        "key": key,
        "data": {"key": key, "name": name},
        "meta": {"numItems": items, "numCollections": subs},
    }


def test_lookup_enriches_matching_keys() -> None:
    zot = _stub_zot([
        _make_collection("AAA", "Risk perception", items=44),
        _make_collection("BBB", "Social Vulnerability", items=53),
        _make_collection("CCC", "Unwanted Collection"),
    ])
    result = lookup_collection_names_and_counts(zot, ["AAA", "BBB"])
    assert result["AAA"]["name"] == "Risk perception"
    assert result["AAA"]["num_items"] == 44
    assert result["BBB"]["name"] == "Social Vulnerability"
    assert result["BBB"]["num_items"] == 53
    # Filter to ONLY the keys we asked about
    assert "CCC" not in result


def test_lookup_handles_missing_keys_gracefully() -> None:
    zot = _stub_zot([_make_collection("AAA", "Found")])
    result = lookup_collection_names_and_counts(zot, ["AAA", "MISSING"])
    assert "AAA" in result
    assert "MISSING" not in result


def test_lookup_empty_input_returns_empty() -> None:
    zot = _stub_zot([_make_collection("A", "n")])
    assert lookup_collection_names_and_counts(zot, []) == {}
    assert lookup_collection_names_and_counts(zot, ["   "]) == {}


def test_lookup_early_exits_when_all_keys_found() -> None:
    """When all requested keys are matched, the function returns without
    walking subsequent pagination chunks — important for vaults with 1000+
    collections where the user only kept 30 of them."""
    matched_call_count = {"n": 0}

    def collections_call(limit=200, start=0):
        matched_call_count["n"] += 1
        if matched_call_count["n"] == 1:
            return [_make_collection("AAA", "Risk perception")]
        return [_make_collection("BBB", "Should not be reached") for _ in range(1)]

    zot = SimpleNamespace(collections=collections_call)
    result = lookup_collection_names_and_counts(zot, ["AAA"])
    # Both matched in chunk 1, so no second call needed
    assert "AAA" in result
    assert matched_call_count["n"] == 1


def test_lookup_normalizes_whitespace_in_input_keys() -> None:
    zot = _stub_zot([_make_collection("AAA", "Real")])
    result = lookup_collection_names_and_counts(zot, ["  AAA  "])
    assert "AAA" in result
