"""Tests for Wave 1: llm_cli.py adapter registry."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# detect_llm_cli - adapter detection
# ---------------------------------------------------------------------------

def test_detect_llm_cli_returns_first_on_path():
    """detect_llm_cli returns the first CLI found on PATH."""
    from research_hub.llm_cli import detect_llm_cli

    with patch("shutil.which", side_effect=lambda n: "/usr/bin/claude" if n == "claude" else None):
        assert detect_llm_cli() == "claude"


def test_detect_llm_cli_returns_none_when_none_on_path():
    """detect_llm_cli returns None when no known CLIs are on PATH."""
    from research_hub.llm_cli import detect_llm_cli

    with patch("shutil.which", return_value=None):
        assert detect_llm_cli() is None


def test_detect_llm_cli_respects_detection_order():
    """detect_llm_cli prefers claude over codex when both are available."""
    from research_hub.llm_cli import detect_llm_cli

    with patch("shutil.which", return_value="/usr/bin/fake"):
        result = detect_llm_cli()
    assert result == "claude"  # claude is first in _DETECTION_ORDER


def test_detect_llm_cli_custom_adapter_found():
    """User-defined adapters are also searched."""
    from research_hub.llm_cli import detect_llm_cli

    custom = {"mycli": {"cmd": ["{path}"], "stdin": True, "hint": "install mycli"}}
    with patch("shutil.which", side_effect=lambda n: "/usr/bin/mycli" if n == "mycli" else None):
        result = detect_llm_cli(user_adapters=custom)
    assert result == "mycli"


# ---------------------------------------------------------------------------
# invoke_llm_cli - adapter invocation
# ---------------------------------------------------------------------------

def test_invoke_llm_cli_claude_uses_stdin():
    """claude adapter passes prompt via stdin."""
    from research_hub.llm_cli import invoke_llm_cli

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = '{"accept": true}'

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=mock_proc) as mock_run,
    ):
        result = invoke_llm_cli("claude", "hello")

    call_kwargs = mock_run.call_args
    assert call_kwargs.kwargs.get("input") == "hello"
    assert result == '{"accept": true}'


def test_invoke_llm_cli_codex_uses_inline_prompt():
    """codex adapter embeds prompt inline in cmd, no stdin."""
    from research_hub.llm_cli import invoke_llm_cli

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "output"

    with (
        patch("shutil.which", return_value="/usr/bin/codex"),
        patch("subprocess.run", return_value=mock_proc) as mock_run,
    ):
        invoke_llm_cli("codex", "my prompt")

    call_kwargs = mock_run.call_args
    cmd = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("args", [])
    assert "my prompt" in cmd
    assert call_kwargs.kwargs.get("input") is None


def test_invoke_llm_cli_unknown_cli_raises():
    """Unknown CLI name raises ValueError."""
    from research_hub.llm_cli import invoke_llm_cli

    with pytest.raises(ValueError, match="Unknown LLM CLI"):
        invoke_llm_cli("unknowncli", "prompt")


def test_invoke_llm_cli_nonzero_exit_raises():
    """Non-zero exit code raises RuntimeError."""
    from research_hub.llm_cli import invoke_llm_cli

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "error"

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        with pytest.raises(RuntimeError):
            invoke_llm_cli("claude", "prompt")


def test_invoke_llm_cli_custom_adapter():
    """User-defined adapter is invoked correctly."""
    from research_hub.llm_cli import invoke_llm_cli

    custom = {"mycli": {"cmd": ["{path}", "--ask"], "stdin": True, "hint": "install mycli"}}
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "ok"

    with (
        patch("shutil.which", return_value="/usr/bin/mycli"),
        patch("subprocess.run", return_value=mock_proc) as mock_run,
    ):
        invoke_llm_cli("mycli", "test prompt", user_adapters=custom)

    cmd = mock_run.call_args.args[0]
    assert cmd == ["/usr/bin/mycli", "--ask"]
    assert mock_run.call_args.kwargs["input"] == "test prompt"


def test_cli_parser_accepts_non_core_llm_cli_names():
    """CLI flags should not block built-in or configured adapters."""
    from research_hub.cli import build_parser

    parser = build_parser()

    summarize_args = parser.parse_args(["summarize", "--cluster", "x", "--llm-cli", "opencode"])
    assert summarize_args.llm_cli == "opencode"

    paper_args = parser.parse_args(["paper", "summarize", "--cli", "aichat"])
    assert paper_args.cli == "aichat"


# ---------------------------------------------------------------------------
# _extract_first_json
# ---------------------------------------------------------------------------

def test_extract_first_json_from_fence():
    """_extract_first_json parses JSON from a markdown code fence."""
    from research_hub.llm_cli import _extract_first_json

    text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
    result = _extract_first_json(text)
    assert result == {"key": "value"}


def test_extract_first_json_bare():
    """_extract_first_json parses bare JSON without fences."""
    from research_hub.llm_cli import _extract_first_json

    text = 'Result: {"accept": true, "score": 4}'
    result = _extract_first_json(text)
    assert result == {"accept": True, "score": 4}


def test_extract_first_json_none_on_no_json():
    """_extract_first_json returns None when no JSON found."""
    from research_hub.llm_cli import _extract_first_json

    assert _extract_first_json("no json here") is None
    assert _extract_first_json("") is None


# ---------------------------------------------------------------------------
# term_overlap_batch
# ---------------------------------------------------------------------------

def test_term_overlap_batch_with_objects():
    """term_overlap_batch works on paper-like objects."""
    from research_hub.fit_check import term_overlap_batch

    papers = [
        SimpleNamespace(abstract="flood risk management adaptation", title="Flood study"),
        SimpleNamespace(abstract="machine learning deep neural", title="ML paper"),
    ]
    scores = term_overlap_batch(papers, ["flood", "risk", "adaptation"])
    assert len(scores) == 2
    assert scores[0] > 0.0   # first paper has flood/risk/adaptation
    assert scores[1] == 0.0  # second paper has none


def test_term_overlap_batch_with_dicts():
    """term_overlap_batch works on dict-style paper objects."""
    from research_hub.fit_check import term_overlap_batch

    papers = [{"abstract": "flood risk", "title": ""}, {"abstract": "", "title": ""}]
    scores = term_overlap_batch(papers, ["flood"])
    assert scores[0] > 0.0
    assert scores[1] == 0.0


def test_term_overlap_batch_empty():
    """term_overlap_batch handles empty list."""
    from research_hub.fit_check import term_overlap_batch

    assert term_overlap_batch([], ["flood"]) == []
