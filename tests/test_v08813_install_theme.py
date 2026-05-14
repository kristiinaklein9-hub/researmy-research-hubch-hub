"""v0.88.13 — `vault install-theme` bundles the Obsidian tech CSS and
flips it on in appearance.json.

Discoverable shortcut so users don't have to copy from the repo's
assets/themes/ dir manually.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# install_theme — happy path
# ---------------------------------------------------------------------------


def test_install_theme_copies_css_and_enables_in_appearance(tmp_path: Path) -> None:
    """Fresh vault: copy CSS + create appearance.json with enabled entry."""
    from research_hub.vault.install_theme import install_theme

    result = install_theme(tmp_path, theme="research-hub-tech")

    assert result.action == "installed"
    assert result.enabled is True
    assert result.errors == []

    css = tmp_path / ".obsidian" / "snippets" / "research-hub-tech.css"
    assert css.exists()
    assert css.stat().st_size > 1000  # rough size sanity (real file is ~7 KB)
    assert "research-hub Tech Aesthetic" in css.read_text(encoding="utf-8")

    appearance = tmp_path / ".obsidian" / "appearance.json"
    assert appearance.exists()
    data = json.loads(appearance.read_text(encoding="utf-8"))
    assert "research-hub-tech" in data["enabledCssSnippets"]


def test_install_theme_skips_when_file_exists_without_force(tmp_path: Path) -> None:
    """Re-running install on an existing snippet is a no-op (skipped_exists)."""
    from research_hub.vault.install_theme import install_theme

    install_theme(tmp_path)  # first run installs
    css = tmp_path / ".obsidian" / "snippets" / "research-hub-tech.css"
    # Tamper to detect overwrite
    css.write_text("/* USER EDITED */\n", encoding="utf-8")

    result = install_theme(tmp_path)
    assert result.action == "skipped_exists"
    # User edit must be preserved (not overwritten)
    assert "USER EDITED" in css.read_text(encoding="utf-8")


def test_install_theme_force_overwrites(tmp_path: Path) -> None:
    """--force replaces the file even if it exists."""
    from research_hub.vault.install_theme import install_theme

    install_theme(tmp_path)
    css = tmp_path / ".obsidian" / "snippets" / "research-hub-tech.css"
    css.write_text("/* TAMPERED */\n", encoding="utf-8")

    result = install_theme(tmp_path, force=True)
    assert result.action == "overwrote"
    # Bundled content is back
    assert "research-hub Tech Aesthetic" in css.read_text(encoding="utf-8")
    assert "TAMPERED" not in css.read_text(encoding="utf-8")


def test_install_theme_preserves_other_appearance_keys(tmp_path: Path) -> None:
    """User-set appearance keys (theme, baseFontSize, etc.) must NOT be lost."""
    from research_hub.vault.install_theme import install_theme

    appearance = tmp_path / ".obsidian" / "appearance.json"
    appearance.parent.mkdir(parents=True)
    appearance.write_text(
        json.dumps({"theme": "obsidian", "baseFontSize": 18}),
        encoding="utf-8",
    )

    install_theme(tmp_path)

    data = json.loads(appearance.read_text(encoding="utf-8"))
    # User keys preserved
    assert data["theme"] == "obsidian"
    assert data["baseFontSize"] == 18
    # Our snippet added
    assert "research-hub-tech" in data["enabledCssSnippets"]


def test_install_theme_does_not_duplicate_in_enabled_list(tmp_path: Path) -> None:
    """Idempotent: running install twice doesn't append a duplicate snippet name."""
    from research_hub.vault.install_theme import install_theme

    install_theme(tmp_path)
    install_theme(tmp_path)  # idempotent

    appearance = tmp_path / ".obsidian" / "appearance.json"
    data = json.loads(appearance.read_text(encoding="utf-8"))
    enabled = data["enabledCssSnippets"]
    assert enabled.count("research-hub-tech") == 1


# ---------------------------------------------------------------------------
# install_theme — error paths
# ---------------------------------------------------------------------------


def test_install_theme_unknown_theme_returns_error(tmp_path: Path) -> None:
    """Bad theme name → no_op + error."""
    from research_hub.vault.install_theme import install_theme

    result = install_theme(tmp_path, theme="some-fake-theme")
    assert result.action == "no_op"
    assert any("unknown theme" in err for err in result.errors)


# ---------------------------------------------------------------------------
# uninstall_theme
# ---------------------------------------------------------------------------


def test_uninstall_theme_removes_css_and_disables(tmp_path: Path) -> None:
    """Uninstall pulls the file + removes from enabledCssSnippets."""
    from research_hub.vault.install_theme import install_theme, uninstall_theme

    install_theme(tmp_path)
    css = tmp_path / ".obsidian" / "snippets" / "research-hub-tech.css"
    assert css.exists()

    result = uninstall_theme(tmp_path)
    assert result.action == "uninstalled"
    assert not css.exists()

    appearance = tmp_path / ".obsidian" / "appearance.json"
    data = json.loads(appearance.read_text(encoding="utf-8"))
    assert "research-hub-tech" not in data.get("enabledCssSnippets", [])


def test_uninstall_when_already_uninstalled_is_no_op(tmp_path: Path) -> None:
    """Uninstall on a fresh vault is a clean no-op (no error)."""
    from research_hub.vault.install_theme import uninstall_theme

    result = uninstall_theme(tmp_path)
    assert result.action == "no_op"
    assert result.errors == []


def test_uninstall_preserves_other_appearance_keys(tmp_path: Path) -> None:
    """Uninstall must not nuke unrelated appearance settings."""
    from research_hub.vault.install_theme import install_theme, uninstall_theme

    appearance = tmp_path / ".obsidian" / "appearance.json"
    appearance.parent.mkdir(parents=True)
    appearance.write_text(
        json.dumps({"theme": "obsidian", "baseFontSize": 16}),
        encoding="utf-8",
    )

    install_theme(tmp_path)
    uninstall_theme(tmp_path)

    data = json.loads(appearance.read_text(encoding="utf-8"))
    assert data["theme"] == "obsidian"
    assert data["baseFontSize"] == 16
    assert "research-hub-tech" not in data.get("enabledCssSnippets", [])
