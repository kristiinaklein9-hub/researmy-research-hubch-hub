"""Regression guard for the rebind auto-create move-robustness fix.

Root cause of the rare full-suite-only flake in
``test_v039_rebind_autocreate.py::test_apply_with_auto_create_new_creates_cluster_and_moves``:
the case-only folder rename in ``_apply_new_cluster_proposals`` did a two-step
``shutil.move`` (source -> ``*.__rebind_tmp__`` -> target) and swallowed any
exception into ``result.errors``. A transient Windows ``shutil.move`` failure
(AV / Search-indexer sharing-violation, WinError 5/32) on the SECOND leg left
``len(moved_files) == 0`` (the observed flake) AND stranded the papers in a
``*.__rebind_tmp__`` folder — a real data-integrity bug for users running
``clusters rebind --auto-create-new`` on Windows.

The fix: ``_robust_move`` retries transient OS errors with backoff, and the
rename branch rolls the temp dir back to the original source name if the second
leg never clears, so papers are NEVER stranded. These tests inject the failure
deterministically so the gate stops flaking and the no-strand contract holds.
"""

from __future__ import annotations

import shutil

import pytest

import research_hub.fsops as fsops
from research_hub.cluster_rebind import _robust_move, apply_rebind, emit_rebind_prompt
from research_hub.clusters import ClusterRegistry
from tests._persona_factory import make_persona_vault


def _seed_orphans(cfg, folder, count, tag="research/topic-x"):
    subdir = cfg.raw / folder
    subdir.mkdir(exist_ok=True)
    for i in range(count):
        (subdir / f"orphan-{i}.md").write_text(
            f"---\ntitle: Orphan {i}\ntags: [{tag}]\n---\nbody",
            encoding="utf-8",
        )


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    """Zero out _robust_move's retry sleep so these tests stay fast.

    The retry logic lives in research_hub.fsops (cluster_rebind re-exports it),
    so patch fsops — the module whose ``time`` / ``shutil`` robust_move uses.
    """
    monkeypatch.setattr(fsops.time, "sleep", lambda *_a, **_k: None)


class _LockMove:
    """A ``shutil.move`` stub that fails when the destination matches ``predicate``.

    ``times=None`` fails forever (persistent lock); ``times=N`` fails the first
    N matching calls then delegates to the real move (transient lock that
    clears). Non-matching destinations always delegate, so unrelated moves and
    the rollback leg succeed.
    """

    def __init__(self, predicate, *, times=None):
        self._real = shutil.move  # capture BEFORE monkeypatching
        self._predicate = predicate
        self._times = times
        self._failures = 0
        self.calls = 0

    def __call__(self, src, dst, *args, **kwargs):
        self.calls += 1
        if self._predicate(str(dst)):
            if self._times is None or self._failures < self._times:
                self._failures += 1
                raise PermissionError(
                    f"[WinError 32] simulated sharing violation writing {dst}"
                )
        return self._real(src, dst, *args, **kwargs)


def _targets_new_cluster(dst: str) -> bool:
    """True only for the lowercase target folder, not the temp/source rollback."""
    return dst.replace("\\", "/").endswith("/behavioral-theory")


def _temp_dirs(raw):
    return [p for p in raw.iterdir() if p.name.endswith(".__rebind_tmp__")]


def _orphan_files(raw):
    return sorted(raw.rglob("orphan-*.md"))


# --------------------------------------------------------------------------- #
# Unit: the retry helper contract
# --------------------------------------------------------------------------- #
def test_robust_move_retries_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("[WinError 32] transient")
        return None  # success on the 3rd attempt

    monkeypatch.setattr(fsops.shutil, "move", flaky)
    _robust_move("src", "dst")  # must not raise
    assert calls["n"] == 3


def test_robust_move_reraises_after_exhausting_attempts(monkeypatch):
    calls = {"n": 0}

    def always_fail(src, dst, *a, **k):
        calls["n"] += 1
        raise PermissionError("[WinError 5] access denied")

    monkeypatch.setattr(fsops.shutil, "move", always_fail)
    with pytest.raises(PermissionError):
        _robust_move("src", "dst")
    assert calls["n"] == fsops._MOVE_RETRY_ATTEMPTS


def test_robust_move_fast_fails_non_permission_errors(monkeypatch):
    """Non-transient OSError subclasses (e.g. FileNotFoundError) must NOT be
    retried — they can't self-heal, and retrying delays the caller's rollback."""
    calls = {"n": 0}

    def missing(src, dst, *a, **k):
        calls["n"] += 1
        raise FileNotFoundError("[WinError 2] the system cannot find the path")

    monkeypatch.setattr(fsops.shutil, "move", missing)
    with pytest.raises(FileNotFoundError):
        _robust_move("src", "dst")
    assert calls["n"] == 1, "permanent errors must propagate on the first attempt"


# --------------------------------------------------------------------------- #
# Integration: the rebind auto-create path
# --------------------------------------------------------------------------- #
def test_rebind_autocreate_recovers_from_transient_lock(tmp_path, monkeypatch):
    """A single transient lock on the temp->target leg must be retried away,
    leaving the exact same successful end-state as a clean run."""
    cfg, _ = make_persona_vault(tmp_path, persona="A")
    _seed_orphans(cfg, "Behavioral-Theory", 6)
    report_path = tmp_path / "rebind.md"
    report_path.write_text(emit_rebind_prompt(cfg), encoding="utf-8")

    stub = _LockMove(_targets_new_cluster, times=1)  # fail once, then clear
    monkeypatch.setattr(fsops.shutil, "move", stub)

    result = apply_rebind(cfg, report_path, dry_run=False, auto_create_new=True)

    assert stub._failures == 1, "the transient failure should have been exercised"
    assert not result.errors, f"transient lock should self-heal, got {result.errors}"
    assert "behavioral-theory" in {c.slug for c in ClusterRegistry(cfg.clusters_file).list()}
    assert len(list((cfg.raw / "behavioral-theory").glob("*.md"))) == 6
    assert len(result.moved) == 6
    assert _temp_dirs(cfg.raw) == [], "no *.__rebind_tmp__ may survive a recovered move"


def test_rebind_autocreate_persistent_lock_never_strands_papers(tmp_path, monkeypatch):
    """If the temp->target leg never clears, the temp dir is rolled back to the
    source name — papers are NEVER stranded in *.__rebind_tmp__, and the error
    is reported instead of being silently lost."""
    cfg, _ = make_persona_vault(tmp_path, persona="A")
    _seed_orphans(cfg, "Behavioral-Theory", 6)
    report_path = tmp_path / "rebind.md"
    report_path.write_text(emit_rebind_prompt(cfg), encoding="utf-8")

    stub = _LockMove(_targets_new_cluster, times=None)  # persistent lock
    monkeypatch.setattr(fsops.shutil, "move", stub)

    result = apply_rebind(cfg, report_path, dry_run=False, auto_create_new=True)

    # The failure is surfaced, not swallowed into a green result.
    assert result.errors, "a persistent move failure must be reported"
    assert any("rolled back" in e for e in result.errors)
    # No partial move recorded.
    assert result.moved == []
    # The cardinal guarantee: nothing stranded, all 6 papers still present.
    assert _temp_dirs(cfg.raw) == [], "papers must not be stranded in *.__rebind_tmp__"
    assert len(_orphan_files(cfg.raw)) == 6, "all 6 papers must survive the failed rebind"
