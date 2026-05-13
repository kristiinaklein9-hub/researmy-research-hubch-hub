"""v0.87.1 #1 — DOI-prefix venue + itemType overrides."""

from __future__ import annotations

from research_hub.zotero.doi_overrides import (
    DOI_PREFIX_OVERRIDES,
    apply_doi_prefix_overrides,
)


def test_zenodo_becomes_dataset_with_blank_venue() -> None:
    pp = {"doi": "10.5281/zenodo.18444869", "journal": "Open MIND", "title": "Data and Code"}
    apply_doi_prefix_overrides(pp)
    assert pp["item_type"] == "dataset"
    assert pp["journal"] == ""


def test_figshare_becomes_dataset_with_blank_venue() -> None:
    pp = {"doi": "10.6084/m9.figshare.12345", "journal": "Some Journal"}
    apply_doi_prefix_overrides(pp)
    assert pp["item_type"] == "dataset"
    assert pp["journal"] == ""


def test_asce_forbids_arxiv_venue() -> None:
    pp = {"doi": "10.1061/9780784486184.086", "journal": "arXiv"}
    apply_doi_prefix_overrides(pp)
    assert pp["item_type"] == "conferencePaper"
    assert pp["journal"] == ""


def test_asce_preserves_legit_venue() -> None:
    pp = {"doi": "10.1061/9780784486184.086", "journal": "Proceedings of WEWRC 2025"}
    apply_doi_prefix_overrides(pp)
    assert pp["item_type"] == "conferencePaper"
    assert pp["journal"] == "Proceedings of WEWRC 2025"


def test_asce_arxiv_check_is_case_insensitive() -> None:
    pp = {"doi": "10.1061/9780784486184.086", "journal": "ARXIV"}
    apply_doi_prefix_overrides(pp)
    assert pp["journal"] == ""
    pp = {"doi": "10.1061/9780784486184.086", "journal": "arXiv Preprint"}
    apply_doi_prefix_overrides(pp)
    assert pp["journal"] == ""


def test_essoar_fills_default_venue() -> None:
    pp = {"doi": "10.22541/essoar.175745445.50927919/v1", "journal": ""}
    apply_doi_prefix_overrides(pp)
    assert pp["journal"] == "ESS Open Archive"
    # item_type unchanged (not set)
    assert "item_type" not in pp


def test_essoar_preserves_existing_venue() -> None:
    pp = {"doi": "10.22541/essoar.175745445.50927919/v1", "journal": "Authorea"}
    apply_doi_prefix_overrides(pp)
    assert pp["journal"] == "Authorea"


def test_egusphere_fills_default_venue() -> None:
    pp = {"doi": "10.5194/egusphere-egu24-15392", "journal": ""}
    apply_doi_prefix_overrides(pp)
    assert pp["journal"] == "EGU General Assembly"


def test_zenodo_idempotent() -> None:
    pp = {"doi": "10.5281/zenodo.18444869", "journal": ""}
    apply_doi_prefix_overrides(pp)
    apply_doi_prefix_overrides(pp)
    assert pp["item_type"] == "dataset"
    assert pp["journal"] == ""


def test_unknown_doi_prefix_no_op() -> None:
    pp = {"doi": "10.1234/abcd.1234.5678", "journal": "Real Journal"}
    apply_doi_prefix_overrides(pp)
    assert "item_type" not in pp
    assert pp["journal"] == "Real Journal"


def test_no_doi_no_op() -> None:
    pp = {"doi": "", "journal": "X"}
    apply_doi_prefix_overrides(pp)
    assert "item_type" not in pp
    assert pp["journal"] == "X"


def test_returns_same_dict_for_chaining() -> None:
    pp = {"doi": "10.5281/zenodo.123", "journal": ""}
    assert apply_doi_prefix_overrides(pp) is pp


def test_longer_prefix_wins() -> None:
    """10.5194/egusphere- must match before 10.5194/ would (if shorter rule existed)."""
    pp = {"doi": "10.5194/egusphere-egu24-15392", "journal": ""}
    apply_doi_prefix_overrides(pp)
    assert pp["journal"] == "EGU General Assembly"


def test_prefix_table_is_complete_for_known_hot_spots() -> None:
    """Sanity check the table contains the prefixes named in V088_PLAN.md §1."""
    expected = {
        "10.5281/zenodo.",
        "10.6084/m9.figshare.",
        "10.1061/",
        "10.22541/essoar.",
        "10.31223/",
        "10.5194/egusphere-",
    }
    assert expected.issubset(set(DOI_PREFIX_OVERRIDES.keys()))
