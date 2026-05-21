"""LLM CLI adapter registry for research-hub.

Provides detect_llm_cli(), invoke_llm_cli(), and _extract_first_json()
moved from auto.py so multiple modules can import without circular deps.

Built-in adapters support claude, codex, gemini, opencode, cursor, aichat.
User-configurable adapters can be added via config.json `llm_cli_adapters` key.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Optional

from research_hub.errors import MissingExternalTool


# Built-in adapter registry. Each entry:
#   cmd:   list of args; {path} is resolved executable path; {prompt} is inline prompt
#   stdin: if True, prompt is passed via stdin; if False, prompt is in cmd (via {prompt})
#   hint:  install hint shown when CLI not found
_BUILTIN_ADAPTERS: dict[str, dict] = {
    "claude": {
        "cmd": ["{path}", "-p"],
        "stdin": True,
        "hint": "npm i -g @anthropic-ai/claude-code",
    },
    "codex": {
        "cmd": ["{path}", "exec", "--full-auto", "{prompt}"],
        "stdin": False,
        "hint": "npm i -g @openai/codex",
    },
    "gemini": {
        "cmd": ["{path}", "--approval-mode", "yolo"],
        "stdin": True,
        "hint": "pip install google-gemini-cli",
    },
    "opencode": {
        "cmd": ["{path}", "run"],
        "stdin": True,
        "hint": "npm i -g opencode",
    },
    "cursor": {
        "cmd": ["{path}", "--repl", "-p"],
        "stdin": True,
        "hint": "Install Cursor IDE (https://cursor.sh)",
    },
    "aichat": {
        "cmd": ["{path}"],
        "stdin": True,
        "hint": "https://github.com/sigoden/aichat",
    },
}

# Detection order - preferred CLIs first
_DETECTION_ORDER = ("claude", "codex", "gemini", "opencode", "aichat", "cursor")


def _patched_auto_symbol(name: str, original):
    """Return a monkeypatched research_hub.auto symbol, when present.

    Older tests and callers patched the back-compat exports on auto.py. The
    canonical implementation lives here now, but honoring those patches keeps
    the public compatibility surface intact.
    """
    auto_mod = sys.modules.get("research_hub.auto")
    if auto_mod is None:
        return None
    candidate = getattr(auto_mod, name, None)
    if candidate is None or candidate is original:
        return None
    if getattr(candidate, "_llm_cli_backcompat_wrapper", False):
        return None
    return candidate


def _load_config_adapters() -> dict:
    """Load user-defined adapters from HubConfig without causing circular imports.

    Returns empty dict on any error (config not initialized, missing key, etc.)
    so callers are always safe to call this.
    """
    try:
        from research_hub.config import get_config  # noqa: PLC0415 (lazy import avoids circular)
        cfg = get_config()
        adapters = getattr(cfg, "llm_cli_adapters", {}) or {}
        return adapters if isinstance(adapters, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _get_adapters(user_adapters: dict | None = None) -> dict[str, dict]:
    """Merge built-in adapters with optional user-defined adapters.

    When user_adapters is None (the default for most callers), adapters are
    loaded automatically from HubConfig.llm_cli_adapters so custom CLIs work
    everywhere without threading user_adapters through every call site.

    Explicit user_adapters={} suppresses config loading (tests, isolated calls).
    """
    adapters = dict(_BUILTIN_ADAPTERS)
    if user_adapters is None:
        user_adapters = _load_config_adapters()
    if user_adapters:
        adapters.update(user_adapters)
    return adapters


def detect_llm_cli(user_adapters: dict | None = None) -> Optional[str]:
    """Return the first LLM CLI found on PATH, or None.

    Detection order: claude -> codex -> gemini -> opencode -> aichat -> cursor,
    then any additional user-defined adapters.

    user_adapters: optional dict from config.json llm_cli_adapters (same schema
    as _BUILTIN_ADAPTERS entries). Pass None to use built-ins only.
    """
    patched = _patched_auto_symbol("detect_llm_cli", detect_llm_cli)
    if patched is not None:
        return patched()

    adapters = _get_adapters(user_adapters)
    # Check built-in order first, then any extra user-defined keys
    detection_order = list(_DETECTION_ORDER) + [
        k for k in adapters if k not in _DETECTION_ORDER
    ]
    for name in detection_order:
        if name in adapters and shutil.which(name):
            return name
    return None


def invoke_llm_cli(
    cli_name: str,
    prompt: str,
    timeout_sec: float = 180.0,
    user_adapters: dict | None = None,
) -> str:
    """Invoke the named LLM CLI with prompt, return stdout.

    Uses the adapter registry to determine invocation pattern:
    - If adapter.stdin=True: prompt passed via stdin
    - If adapter.stdin=False: {prompt} placeholder in cmd is replaced with prompt

    v0.50.1 behaviour preserved: resolve full path via shutil.which() so
    Windows npm .cmd shims are found (subprocess needs PATHEXT-extended paths).
    """
    patched = _patched_auto_symbol("_invoke_llm_cli", invoke_llm_cli)
    if patched is not None:
        return patched(cli_name, prompt)

    adapters = _get_adapters(user_adapters)
    adapter = adapters.get(cli_name)
    if adapter is None:
        raise ValueError(
            f"Unknown LLM CLI '{cli_name}'. Known: {list(adapters)}"
        )

    resolved = shutil.which(cli_name)
    if not resolved:
        raise MissingExternalTool(
            cli_name,
            install_hint=adapter.get("hint", f"Install {cli_name} and add it to PATH"),
        )

    use_stdin = adapter.get("stdin", True)
    cmd = [
        resolved if arg == "{path}" else (prompt if arg == "{prompt}" else arg)
        for arg in adapter["cmd"]
    ]
    stdin_input = prompt if use_stdin else None

    proc = subprocess.run(
        cmd,
        input=stdin_input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cli_name} exited {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


# ---------------------------------------------------------------------------
# Back-compat aliases - old callers used _invoke_llm_cli (private name)
# ---------------------------------------------------------------------------
_invoke_llm_cli = invoke_llm_cli  # noqa: N816 (keep old name for any lingering internal refs)


def _extract_first_json(text: str) -> Optional[dict]:
    """Find the first valid JSON object in `text`, ignoring code fences and prose."""
    patched = _patched_auto_symbol("_extract_first_json", _extract_first_json)
    if patched is not None:
        return patched(text)

    if not text:
        return None
    fence_starts = [i for i in range(len(text)) if text.startswith("```", i)]
    candidates: list[str] = []
    for i in range(0, len(fence_starts) - 1, 2):
        start = fence_starts[i]
        end = fence_starts[i + 1]
        block = text[start + 3 : end]
        if block.lstrip().lower().startswith("json"):
            block = block.split("\n", 1)[1] if "\n" in block else ""
        candidates.append(block)
    candidates.append(text)
    for c in candidates:
        c = c.strip()
        first_brace = c.find("{")
        last_brace = c.rfind("}")
        if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
            continue
        try:
            return json.loads(c[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            continue
    return None
