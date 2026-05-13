from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from research_hub.notebooklm.upload import (
    _title_rule_match,
    validate_uploaded_sources,
)


@pytest.mark.parametrize(
    ("title", "rule"),
    [
        ("IEEE Xplore - Unable to Load Page", "unable_to_load"),
        ("Error 404", "error"),
        ("Abstract EGU24-15392", "abstract_id_pattern"),
        ("LLMs for Water Distribution Systems | Vol , No", "blank_vol_no"),
        ("Page Not Found", "page_not_found"),
        ("403 Forbidden", "forbidden_403"),
    ],
)
def test_bad_title_patterns_match(title: str, rule: str) -> None:
    match = _title_rule_match(title)
    assert match is not None
    assert match[0] == rule


@pytest.mark.parametrize(
    "title",
    [
        "Large Language Models for Water Distribution Systems",
        "Abstract Reasoning with Hydrologic Agents",
        "Journal of Water Resources | Vol 12, No 4",
    ],
)
def test_bad_title_patterns_reject_benign_titles(title: str) -> None:
    assert _title_rule_match(title) is None


def test_short_fulltext_threshold_exempts_zenodo_dataset() -> None:
    client = _Client(
        [
            _source("s1", "Zenodo Dataset"),
            _source("s2", "Sparse Web Page"),
        ],
        chars=150,
    )
    handle = SimpleNamespace(notebook_id="nb1", name="Notebook")

    report = validate_uploaded_sources(
        client,
        handle,
        ["10.5281/zenodo.12345", "10.1000/short"],
        cluster_slug="alpha",
    )

    assert report.suspicious_count == 1
    assert report.suspicious[0].doi == "10.1000/short"
    assert report.suspicious[0].matched_rule == "short_fulltext"


def test_validator_warning_and_sidecar_for_mixed_sources(tmp_path) -> None:
    client = _Client(
        [
            _source("s1", "Good Paper"),
            _source("s2", "IEEE Xplore - Unable to Load Page"),
            _source("s3", "Abstract EGU24-15392"),
            _source("s4", "LLMs for Water Distribution Systems | Vol , No"),
        ],
        chars=5000,
    )
    handle = SimpleNamespace(notebook_id="nb1", name="Notebook")
    artifacts_dir = tmp_path / ".research_hub" / "artifacts" / "human-water-llm"

    report = validate_uploaded_sources(
        client,
        handle,
        [
            "10.1000/good",
            "10.1109/iciprob69625.2026.11497793",
            "10.5194/egusphere-egu24-15392",
            "10.1061/9780784486184.086",
        ],
        cluster_slug="human-water-llm",
        artifacts_dir=artifacts_dir,
    )

    warning = report.warning_text()
    assert "[warn] 3 source(s)" in warning
    assert "10.1109/iciprob69625.2026.11497793" in warning
    assert "matched: unable to load" in warning
    sidecar = json.loads((artifacts_dir / ".ingest_validation.json").read_text(encoding="utf-8"))
    assert sidecar["cluster_slug"] == "human-water-llm"
    assert sidecar["total"] == 4
    assert sidecar["suspicious_count"] == 3
    assert [entry["matched_rule"] for entry in sidecar["suspicious"]] == [
        "unable_to_load",
        "abstract_id_pattern",
        "blank_vol_no",
    ]


def test_validator_clean_upload_has_no_warning(tmp_path) -> None:
    sources = [_source(f"s{i}", f"Normal Source {i}") for i in range(12)]
    client = _Client(sources, chars=3000)
    handle = SimpleNamespace(notebook_id="nb1", name="Notebook")

    report = validate_uploaded_sources(
        client,
        handle,
        [f"10.1000/{i}" for i in range(12)],
        cluster_slug="clean",
        artifacts_dir=tmp_path / ".research_hub" / "artifacts" / "clean",
    )

    assert report.suspicious_count == 0
    assert report.warning_text() == ""


def _source(source_id: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(id=source_id, title=title)


class _Sources:
    def __init__(self, sources: list[SimpleNamespace], chars: int) -> None:
        self._sources = sources
        self._chars = chars

    def list(self, notebook_id: str) -> list[SimpleNamespace]:
        assert notebook_id == "nb1"
        return self._sources

    def fulltext(self, source_id: str) -> SimpleNamespace:
        return SimpleNamespace(source_id=source_id, content="x" * self._chars, char_count=self._chars)


class _Client:
    def __init__(self, sources: list[SimpleNamespace], *, chars: int) -> None:
        self.sources = _Sources(sources, chars)
