"""v0.87.1 #1 — DOI-prefix overrides for known-misattributed venues.

V2 audit (Agent 2 post-v0.87) found that on the human-water-llm
shakedown the search-backend layer assigned wrong metadata to two
papers that would be rejected by a manuscript reviewer:

- goldshtein2025  DOI=10.1061/9780784486184.086 (ASCE WEWRC 2025)
                  journal field said "arXiv" — factually wrong
- arnold2026      DOI=10.5281/zenodo.18444869 (Zenodo dataset)
                  itemType="journalArticle", journal="Open MIND"
                  — should be itemType="dataset"

Both bugs are upstream of `enrich.py`'s empty-field filler — that
module only fills BLANK fields, never overrides existing wrong ones.
The fix is to run a DOI-prefix-pattern check BEFORE the Zotero
template is built so wrong venues get cleaned at ingest time.

Locked decision (V088_PLAN.md §1): use DOI prefix as the trigger,
not e.g. publisher inference, because prefix is stable and the
mapping is unambiguous (10.1061 ⇒ ASCE, 10.5281/zenodo ⇒ Zenodo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PrefixOverride:
    """How to rewrite a paper dict when its DOI starts with this prefix.

    Order of operations:
    1. If `item_type` is set, override the Zotero template type.
    2. If `forbid_venue` matches the incoming venue (case-insensitive),
       blank the venue (later steps may refill it).
    3. If `force_venue_blank` is True, always blank the venue.
    4. If `default_venue` is set and the venue is now blank, fill it.
    """

    item_type: str = ""
    forbid_venue: frozenset[str] = field(default_factory=frozenset)
    force_venue_blank: bool = False
    default_venue: str = ""


# DOI prefixes mapped to override rules.
# Order matters: longer/more-specific prefixes are checked first.
DOI_PREFIX_OVERRIDES: dict[str, PrefixOverride] = {
    # ─────── datasets ───────────────────────────────────────────
    # Zenodo: dataset by default. "Open MIND" / random journal-y
    # strings auto-attached by some backends should be blanked.
    "10.5281/zenodo.": PrefixOverride(
        item_type="dataset",
        force_venue_blank=True,
    ),
    # Figshare: also a dataset repository.
    "10.6084/m9.figshare.": PrefixOverride(
        item_type="dataset",
        force_venue_blank=True,
    ),
    # ─────── ASCE: never arXiv ──────────────────────────────────
    # ASCE proceedings + journals all sit under 10.1061. Some
    # backends fall back to "arXiv" if Crossref didn't return a
    # venue — that's wrong.
    "10.1061/": PrefixOverride(
        item_type="conferencePaper",
        forbid_venue=frozenset({"arxiv", "arxiv preprint"}),
    ),
    # ─────── preprint platforms ─────────────────────────────────
    # ESS Open Archive (Authorea) — sociohydrology / earth-science
    # preprints. Venue defaults to blank from Crossref; fill it.
    "10.22541/essoar.": PrefixOverride(
        default_venue="ESS Open Archive",
    ),
    # EarthArXiv.
    "10.31223/": PrefixOverride(
        default_venue="EarthArXiv",
    ),
    # SSRN preprints.
    "10.2139/ssrn.": PrefixOverride(
        default_venue="SSRN",
    ),
    # ─────── conference / abstract platforms ────────────────────
    # EGU General Assembly abstracts.
    "10.5194/egusphere-": PrefixOverride(
        default_venue="EGU General Assembly",
    ),
    # ─────── arXiv preprint DOIs ────────────────────────────────
    # arXiv assigned-DOI form (10.48550/arxiv.NNNN.NNNNN) sometimes
    # comes in with a wrong journal field copied from a later
    # publication. If the venue isn't arXiv, leave it; the helper
    # in pipeline.py already special-cases this for fetch.py.
    "10.48550/arxiv.": PrefixOverride(
        default_venue="arXiv",
    ),
}


def _matching_prefix(doi: str) -> tuple[str, PrefixOverride] | None:
    """Return the longest matching prefix + its override, or None."""
    if not doi:
        return None
    doi_norm = doi.strip().lower()
    # Sort longest-first so 10.5194/egusphere- beats 10.5194/
    for prefix in sorted(DOI_PREFIX_OVERRIDES, key=len, reverse=True):
        if doi_norm.startswith(prefix):
            return prefix, DOI_PREFIX_OVERRIDES[prefix]
    return None


def apply_doi_prefix_overrides(pp: dict[str, Any]) -> dict[str, Any]:
    """Mutate `pp` in place to apply DOI-prefix overrides; return same dict.

    Looks at `pp["doi"]`, finds the longest matching prefix in
    DOI_PREFIX_OVERRIDES, and applies its rules to `pp["journal"]`
    (the field name research-hub uses for "venue") and to a new
    `pp["item_type"]` field that pipeline.py reads when picking the
    Zotero template.

    Idempotent: re-running on an already-corrected pp is a no-op.

    Returns the same `pp` for chaining.
    """
    match = _matching_prefix(str(pp.get("doi", "") or ""))
    if match is None:
        return pp
    _prefix, rule = match

    if rule.item_type:
        pp["item_type"] = rule.item_type

    current_venue = str(pp.get("journal", "") or "").strip()
    if rule.force_venue_blank:
        pp["journal"] = ""
    elif rule.forbid_venue and current_venue.lower() in rule.forbid_venue:
        pp["journal"] = ""

    if rule.default_venue and not str(pp.get("journal", "") or "").strip():
        pp["journal"] = rule.default_venue

    return pp
