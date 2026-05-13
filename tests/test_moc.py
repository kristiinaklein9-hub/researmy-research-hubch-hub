from __future__ import annotations

from research_hub.vault.hub_overview import derive_moc_links, ensure_moc


def test_ensure_moc_creates_file_when_missing(tmp_path):
    path = ensure_moc(tmp_path, "LLM-Agents", description="LLM agent notes.")

    assert path == tmp_path / "hub" / "_moc" / "LLM-Agents.md"
    text = path.read_text(encoding="utf-8")
    assert "type: moc" in text
    assert "name: LLM-Agents" in text
    assert 'tags: ["topic:llm-agents", "type:moc"]' in text
    assert "# LLM-Agents" in text
    assert "LLM agent notes." in text


def test_ensure_moc_is_idempotent(tmp_path):
    path = ensure_moc(tmp_path, "Water-Resources")
    path.write_text("USER EDIT\n", encoding="utf-8")

    second = ensure_moc(tmp_path, "Water-Resources", description="ignored")

    assert second == path
    assert path.read_text(encoding="utf-8") == "USER EDIT\n"


def test_moc_links_from_llm_and_water_slugs():
    assert derive_moc_links("human-water-llm") == ["LLM-Agents", "Water-Resources"]
    assert derive_moc_links("flood-water-supply") == ["Water-Resources"]
    assert derive_moc_links("social-llm-agents") == ["LLM-Agents"]
