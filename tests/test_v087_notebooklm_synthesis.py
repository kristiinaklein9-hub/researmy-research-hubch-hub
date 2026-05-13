"""v0.87 N2 — the default brief is now a cluster-synthesis CUSTOM report.

The pre-v0.87 path used `ReportFormat.BRIEFING_DOC`, which is
NotebookLM's auto-briefing template — it picks one source and writes
about it. For multi-paper clusters that produces a 1-of-N brief
(observed: 11/12 papers got 0 mentions in the Flood-LLM-only brief
for the human-water-llm shakedown). The new path uses
`ReportFormat.CUSTOM` with `CLUSTER_SYNTHESIS_PROMPT` so coverage
spans all sources.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from notebooklm import ReportFormat

from research_hub.notebooklm.client import (
    CLUSTER_SYNTHESIS_PROMPT,
    NotebookLMClient,
)


def _build_client_with_capture() -> tuple[NotebookLMClient, MagicMock]:
    """Build a NotebookLMClient whose artifacts.generate_report is captured."""
    upstream = MagicMock()
    upstream.artifacts.generate_report = AsyncMock(
        return_value=SimpleNamespace(task_id="t1", id="t1", status="DONE")
    )
    upstream.artifacts.wait_for_completion = AsyncMock(
        return_value=SimpleNamespace(status="DONE", artifact_id="a1")
    )

    @asynccontextmanager
    async def _ctx():
        yield upstream

    client = NotebookLMClient.__new__(NotebookLMClient)
    client._client = upstream
    client._timeout = 60
    client._loop = asyncio.new_event_loop()
    client._active_notebook_id = "NB123"

    def _run(coro):
        return client._loop.run_until_complete(coro)

    client._run = _run  # type: ignore[attr-defined]
    return client, upstream


def test_cluster_synthesis_prompt_is_loaded_at_module_level() -> None:
    """Sanity: the prompt is non-empty and mentions synthesis intent."""
    assert isinstance(CLUSTER_SYNTHESIS_PROMPT, str)
    assert len(CLUSTER_SYNTHESIS_PROMPT) > 100
    lowered = CLUSTER_SYNTHESIS_PROMPT.lower()
    assert "synthesize" in lowered
    assert "across all sources" in lowered or "all sources" in lowered
    assert "open questions" in lowered


def test_trigger_briefing_uses_custom_format_with_synthesis_prompt() -> None:
    """The brief generation path must use CUSTOM + CLUSTER_SYNTHESIS_PROMPT,
    not the legacy BRIEFING_DOC single-source template."""
    client, upstream = _build_client_with_capture()
    try:
        client.trigger_briefing(notebook_id="NB123")
    except Exception:
        # We don't care about the full sync-adapter return chain — we only
        # want to see what call landed on the upstream RPC.
        pass

    assert upstream.artifacts.generate_report.await_count == 1
    call = upstream.artifacts.generate_report.await_args
    # positional: notebook_id
    assert call.args[0] == "NB123"
    # keyword args set the synthesis behavior
    assert call.kwargs["report_format"] == ReportFormat.CUSTOM
    assert call.kwargs["custom_prompt"] == CLUSTER_SYNTHESIS_PROMPT
