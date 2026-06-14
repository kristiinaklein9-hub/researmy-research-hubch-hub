"""v1.0.11 Obsidian-graph regression tests (P1-4).

P1-4b: the backward footer-append path re-ranks by tag overlap and caps at the
same top-N (10) as the forward path, so a busy cluster's footers can no longer
grow unbounded (the O(n^2) clique tax).
"""

from __future__ import annotations

import re
from pathlib import Path

from research_hub.vault.link_updater import RELATED_SECTION_HEADER, update_cluster_links


def _write(cdir: Path, slug: str, tags: list[str], footer_slugs: list[str] | None = None) -> None:
    tag_list = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
    body = f'---\ntitle: "{slug}"\ntags: {tag_list}\ntopic_cluster: "c"\n---\n'
    if footer_slugs:
        body += "\n" + RELATED_SECTION_HEADER + "\n" + "\n".join(f"- [[{s}]]" for s in footer_slugs) + "\n"
    (cdir / f"{slug}.md").write_text(body, encoding="utf-8")


def test_update_cluster_links_caps_backward_footer_at_10(tmp_path):
    raw = tmp_path / "raw"
    cdir = raw / "c"
    cdir.mkdir(parents=True)

    # 10 plain siblings + a hub whose footer is already AT the cap (10).
    siblings = [f"p{i}" for i in range(10)]
    for s in siblings:
        _write(cdir, s, ["llm", "agents"])
    _write(cdir, "hub", ["llm", "agents", "x"], footer_slugs=siblings)

    # A new note that shares an extra tag with hub (so it out-ranks one sibling
    # in hub's footer) — the OLD code appended it as an 11th entry; the re-cap
    # keeps the footer at 10, dropping the lowest-overlap sibling.
    _write(cdir, "newpaper", ["llm", "agents", "x"])
    update_cluster_links(cdir / "newpaper.md", raw, "c")

    hub_footer = re.findall(r"\[\[([^\]]+)\]\]", (cdir / "hub.md").read_text(encoding="utf-8"))
    assert len(hub_footer) <= 10  # P1-4b: capped, did NOT grow to 11
    assert "newpaper" in hub_footer  # bidirectional link still established (out-ranked a sibling)


# --------------------------------------------------------------------------- #
# P1-4a: paper notes link the sub-MOC ONLY; pages keep the parent
# --------------------------------------------------------------------------- #
def test_derive_moc_links_paper_note_omits_parent():
    from research_hub.vault.hub_overview import derive_moc_links

    page = derive_moc_links("llm-agents-consumer-behavior")
    note = derive_moc_links("llm-agents-consumer-behavior", for_paper_note=True)

    # Pages keep BOTH the parent + the sub-MOC (so the parent stays referenced —
    # the v1.0.7 merged-cluster GC relies on this to protect it).
    assert "LLM-Agents" in page
    assert any(n.startswith("LLM-Agents-") for n in page)
    # Paper notes drop the bare parent, keep only the sub-MOC (kills the clique).
    assert "LLM-Agents" not in note
    assert any(n.startswith("LLM-Agents-") for n in note)


def test_gc_parent_protection_survives_paper_note_split(tmp_path, monkeypatch):
    """The v1.0.7 merged-cluster GC must still protect the shared parent MOC —
    it uses derive_moc_links in PAGE mode (default), so P1-4a doesn't weaken it."""
    from research_hub.vault.hub_overview import derive_moc_links

    # The GC's links_for() uses the default (page) variant → parent present.
    assert "LLM-Agents" in derive_moc_links("llm-agents-consumer-behavior")


# --------------------------------------------------------------------------- #
# P1-4c: prune-footers fixes already-bloated footers
# --------------------------------------------------------------------------- #
def test_prune_footers_caps_bloated_dry_run_then_apply(tmp_path):
    from research_hub.vault.link_updater import prune_footers

    raw = tmp_path / "raw"
    cdir = raw / "c"
    cdir.mkdir(parents=True)
    slugs = [f"p{i}" for i in range(12)]
    for s in slugs:
        _write(cdir, s, ["llm", "agents"])
    _write(cdir, "bloated", ["llm", "agents"], footer_slugs=slugs)  # 12-entry footer (over cap)

    # dry-run: reports would-change, writes nothing
    report = prune_footers(raw, top_n=8, apply=False)
    assert report["would_change"] >= 1 and report["changed"] == 0
    before = re.findall(r"\[\[([^\]]+)\]\]", (cdir / "bloated.md").read_text(encoding="utf-8"))
    assert len(before) == 12  # untouched in dry-run

    # apply: caps to top-8
    report2 = prune_footers(raw, top_n=8, apply=True)
    assert report2["changed"] >= 1
    after = re.findall(r"\[\[([^\]]+)\]\]", (cdir / "bloated.md").read_text(encoding="utf-8"))
    assert len(after) <= 8
