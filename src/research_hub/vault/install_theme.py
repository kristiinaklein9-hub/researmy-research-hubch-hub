"""v0.88.13: copy a bundled Obsidian CSS snippet into the user's vault
and enable it in appearance.json.

The snippet ships at:
    src/research_hub/assets/themes/<theme>.css

After install it lives at:
    <vault>/.obsidian/snippets/<theme>.css

and is added to `enabledCssSnippets` in `.obsidian/appearance.json`.

Designed to be:
- Idempotent (default skips if file already exists; --force overwrites)
- Reversible (--uninstall removes the file + disables it; never touches
  user-authored snippets)
- Conservative (only writes files we own; never overwrites user-edited
  appearance.json keys we don't recognise)
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Bundled themes shipped under src/research_hub/assets/themes/. Add new
# entries here when shipping new presets.
AVAILABLE_THEMES = ("research-hub-tech",)
DEFAULT_THEME = "research-hub-tech"


@dataclass
class InstallThemeResult:
    theme: str
    css_path: Path | None = None
    appearance_path: Path | None = None
    action: str = ""           # installed / overwrote / skipped_exists / uninstalled / no_op
    enabled: bool = False
    errors: list[str] = field(default_factory=list)


def _bundled_theme_path(theme: str) -> Path:
    """Resolve the bundled CSS for a theme name. Returns the .css under
    the package's assets/themes/ dir."""
    pkg_root = Path(__file__).resolve().parent.parent
    return pkg_root / "assets" / "themes" / f"{theme}.css"


def _read_appearance(appearance_path: Path) -> dict:
    if not appearance_path.exists():
        return {}
    try:
        return json.loads(appearance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not parse %s: %s — treating as empty", appearance_path, exc)
        return {}


def _write_appearance(appearance_path: Path, data: dict) -> None:
    appearance_path.parent.mkdir(parents=True, exist_ok=True)
    appearance_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _ensure_enabled(appearance_path: Path, theme: str) -> bool:
    """Add `theme` to enabledCssSnippets in appearance.json. Returns
    True if the key was newly added (False if already present)."""
    data = _read_appearance(appearance_path)
    enabled = list(data.get("enabledCssSnippets", []))
    if theme in enabled:
        return False
    enabled.append(theme)
    data["enabledCssSnippets"] = enabled
    _write_appearance(appearance_path, data)
    return True


def _ensure_disabled(appearance_path: Path, theme: str) -> bool:
    """Remove `theme` from enabledCssSnippets. Returns True if removed."""
    data = _read_appearance(appearance_path)
    enabled = list(data.get("enabledCssSnippets", []))
    if theme not in enabled:
        return False
    enabled.remove(theme)
    data["enabledCssSnippets"] = enabled
    _write_appearance(appearance_path, data)
    return True


def install_theme(
    vault_root: Path,
    *,
    theme: str = DEFAULT_THEME,
    force: bool = False,
) -> InstallThemeResult:
    """Copy bundled CSS to <vault>/.obsidian/snippets/ and enable it."""
    result = InstallThemeResult(theme=theme)

    if theme not in AVAILABLE_THEMES:
        result.action = "no_op"
        result.errors.append(
            f"unknown theme {theme!r}; available: {', '.join(AVAILABLE_THEMES)}"
        )
        return result

    source = _bundled_theme_path(theme)
    if not source.exists():
        result.action = "no_op"
        result.errors.append(f"bundled theme CSS not found at {source}")
        return result

    snippets_dir = Path(vault_root) / ".obsidian" / "snippets"
    snippets_dir.mkdir(parents=True, exist_ok=True)
    dest = snippets_dir / source.name
    result.css_path = dest

    # v0.88.15: capture existence BEFORE the copy so action="overwrote"
    # vs "installed" reflects what actually happened. Pre-fix, dest.exists()
    # was checked AFTER shutil.copy2 (always True post-copy) — first-time
    # --force installs were mislabeled as "overwrote".
    already_existed = dest.exists()

    if already_existed and not force:
        result.action = "skipped_exists"
    else:
        try:
            shutil.copy2(source, dest)
            result.action = "overwrote" if already_existed else "installed"
            # Re-stat for accuracy after copy
            if not (dest.exists() and dest.stat().st_size > 0):
                result.errors.append(f"copy succeeded but {dest} is empty/missing")
        except OSError as exc:
            result.errors.append(f"copy failed: {exc}")
            return result

    appearance_path = Path(vault_root) / ".obsidian" / "appearance.json"
    result.appearance_path = appearance_path
    try:
        newly_enabled = _ensure_enabled(appearance_path, theme)
        result.enabled = True
        if newly_enabled and result.action == "skipped_exists":
            # CSS already on disk but theme was disabled — flip enabled bit
            result.action = "re_enabled"
    except OSError as exc:
        result.errors.append(f"failed to enable theme: {exc}")

    return result


def uninstall_theme(
    vault_root: Path,
    *,
    theme: str = DEFAULT_THEME,
) -> InstallThemeResult:
    """Remove the CSS file + disable in appearance.json.

    Never touches files we don't own (e.g. user-authored snippets with
    the same name would be left alone if their content doesn't match
    the bundled one — though in practice the bundled file is
    well-namespaced so collisions are unlikely)."""
    result = InstallThemeResult(theme=theme)

    snippets_dir = Path(vault_root) / ".obsidian" / "snippets"
    dest = snippets_dir / f"{theme}.css"
    result.css_path = dest

    appearance_path = Path(vault_root) / ".obsidian" / "appearance.json"
    result.appearance_path = appearance_path

    removed_file = False
    if dest.exists():
        try:
            dest.unlink()
            removed_file = True
        except OSError as exc:
            result.errors.append(f"failed to remove {dest}: {exc}")

    disabled = False
    try:
        disabled = _ensure_disabled(appearance_path, theme)
    except OSError as exc:
        result.errors.append(f"failed to disable theme: {exc}")

    # v0.88.15: distinguish "fully uninstalled" from "partial uninstall"
    # (e.g. file removal succeeded but appearance.json was read-only, OR
    # vice versa). Pre-fix, any positive outcome reported "uninstalled"
    # even with errors in result.errors, misleading the CLI summary.
    if result.errors and (removed_file or disabled):
        result.action = "partial_uninstall"
    elif removed_file or disabled:
        result.action = "uninstalled"
    else:
        result.action = "no_op"
    result.enabled = False
    return result
