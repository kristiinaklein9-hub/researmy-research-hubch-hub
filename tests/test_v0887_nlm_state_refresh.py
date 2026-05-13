"""v0.88.7 — NotebookLMClient persists rotated cookies on close().

Root cause for the user's recurring "Authentication expired" pain:
Google rotates short-lived auth tokens (SIDCC / SIDTS / OSID / CSRF)
each session, but research-hub never wrote them back to state.json.
After v0.88.7, ``close()`` (and the opt-in ``refresh_and_save()``)
call notebooklm-py's ``save_cookies_to_storage`` to persist the
freshly-rotated jar.

These tests verify the wiring without spinning up a real Google
session.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def saved_calls(monkeypatch):
    """Capture every save_cookies_to_storage invocation."""
    calls: list[tuple[object, Path]] = []

    def fake_save(cookie_jar, path):
        calls.append((cookie_jar, Path(str(path))))

    # Patch in notebooklm.auth so the import inside _save_state picks it up.
    import notebooklm.auth as upstream_auth

    monkeypatch.setattr(upstream_auth, "save_cookies_to_storage", fake_save)
    return calls


class _FakeUpstream:
    """Stand-in for notebooklm.NotebookLMClient — no real Google calls."""

    def __init__(self, *, storage_path: str):
        self.auth = SimpleNamespace(
            cookies={},
            cookie_jar=object(),
            csrf_token="csrf",
            session_id="sid",
            storage_path=storage_path,
        )
        self.refresh_called = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def refresh_auth(self):
        self.refresh_called += 1
        return self.auth


@pytest.fixture
def fake_client_class(monkeypatch):
    """Patch notebooklm-py's upstream client constructor so we never
    touch the network. ``from_storage`` returns a coroutine that yields
    a fresh ``_FakeUpstream`` whose storage_path matches the caller's."""

    async def fake_from_storage(*, path, timeout=None, **kwargs):
        return _FakeUpstream(storage_path=str(path))

    monkeypatch.setattr(
        "notebooklm.NotebookLMClient.from_storage",
        staticmethod(fake_from_storage),
    )
    return fake_from_storage


def test_close_persists_rotated_cookies_to_state_json(
    tmp_path: Path, saved_calls: list, fake_client_class
) -> None:
    from research_hub.notebooklm.client import NotebookLMClient

    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")

    client = NotebookLMClient(state, headless=True, timeout_sec=10)
    client.close()

    assert len(saved_calls) == 1, "close() must call save_cookies_to_storage once"
    _, saved_path = saved_calls[0]
    assert saved_path == state


def test_close_save_state_is_best_effort_on_failure(
    tmp_path: Path, monkeypatch, fake_client_class
) -> None:
    """A failing save_cookies_to_storage must NOT raise out of close()."""
    import notebooklm.auth as upstream_auth

    def boom(*args, **kwargs):
        raise RuntimeError("cookie file is read-only")

    monkeypatch.setattr(upstream_auth, "save_cookies_to_storage", boom)

    from research_hub.notebooklm.client import NotebookLMClient

    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")

    client = NotebookLMClient(state, headless=True, timeout_sec=10)
    # Must not raise
    client.close()


def test_refresh_and_save_calls_both_refresh_and_save(
    tmp_path: Path, saved_calls: list, fake_client_class
) -> None:
    from research_hub.notebooklm.client import NotebookLMClient

    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")

    client = NotebookLMClient(state, headless=True, timeout_sec=10)
    client.refresh_and_save()

    assert client._client.refresh_called == 1
    assert len(saved_calls) == 1, "refresh_and_save() must persist after refresh"

    # close() also persists → total 2 saves over this client's lifetime
    client.close()
    assert len(saved_calls) == 2


def test_save_state_skipped_when_no_storage_path(
    tmp_path: Path, saved_calls: list, monkeypatch
) -> None:
    """Env-var-loaded auth has ``storage_path=None`` — skip the save
    rather than crashing or writing to an unintended file."""

    class _EnvAuthUpstream(_FakeUpstream):
        def __init__(self, *, storage_path):
            super().__init__(storage_path=storage_path)
            # Mimic NOTEBOOKLM_AUTH_JSON env-var auth path.
            self.auth.storage_path = None

    async def fake_from_storage(*, path, timeout=None, **kwargs):
        return _EnvAuthUpstream(storage_path=str(path))

    monkeypatch.setattr(
        "notebooklm.NotebookLMClient.from_storage",
        staticmethod(fake_from_storage),
    )

    from research_hub.notebooklm.client import NotebookLMClient

    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    client = NotebookLMClient(state, headless=True, timeout_sec=10)
    client.close()

    assert saved_calls == [], "no save when storage_path is None"
