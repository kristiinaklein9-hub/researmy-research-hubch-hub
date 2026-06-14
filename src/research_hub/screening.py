"""PRISMA screening provenance log (v1.1 P2-3).

Append-only JSONL at ``.research_hub/screening_log.jsonl`` — one record per
screen/ingest decision. Lets a systematic-review workflow render the PRISMA
flow numbers (identified / deduped / screened-out / included) and audit WHY any
paper was excluded — the clearest differentiator from a glorified bookmark
folder, and it closes the silent-topic-drift hole (a kept-but-unscreened paper
now leaves a trace).

v1.1 auto-emits the SCREEN stage (``included`` / ``screened_out`` / kept-but-
``unverified``) from the no-LLM relevance gate. The ``identified`` / ``deduped``
stages are schema-ready for the upstream discover/dedup steps to emit later.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_NAME = "screening_log.jsonl"

# PRISMA-ish funnel stages. A kept-but-unscreened paper is recorded as
# ``included`` with ``unverified=True`` (one record per paper decision), not as a
# separate stage — so ``included`` always totals every kept paper.
STAGE_IDENTIFIED = "identified"
STAGE_DEDUPED = "deduped"
STAGE_SCREENED_OUT = "screened_out"
STAGE_INCLUDED = "included"
STAGES = (
    STAGE_IDENTIFIED,
    STAGE_DEDUPED,
    STAGE_SCREENED_OUT,
    STAGE_INCLUDED,
)


def _log_path(cfg) -> Path:
    return Path(cfg.research_hub_dir) / LOG_NAME


def record_screening(
    cfg,
    *,
    stage: str,
    cluster: str,
    doi: str = "",
    arxiv: str = "",
    title: str = "",
    reason: str = "",
    unverified: bool = False,
    ts: str | None = None,
) -> dict:
    """Append one screening-decision record and return it.

    Best-effort: a write failure is swallowed (provenance logging must never
    break an ingest). ``ts`` is injectable for deterministic tests. ``unverified``
    marks an ``included`` paper the no-LLM gate kept without a clear verdict.
    """
    record = {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "cluster": cluster,
        "doi": (doi or "").strip(),
        "arxiv": (arxiv or "").strip(),
        "title": (title or "").strip(),
        "reason": (reason or "").strip(),
        "unverified": bool(unverified),
    }
    try:
        path = _log_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return record


def read_screening_log(cfg, *, cluster: str | None = None) -> list[dict]:
    """Read all screening records (optionally filtered to one cluster).

    Malformed lines are skipped — the log is append-only and best-effort, so a
    torn write must not poison the whole read.
    """
    path = _log_path(cfg)
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if cluster is not None and rec.get("cluster") != cluster:
            continue
        records.append(rec)
    return records


def prisma_counts(records: list[dict]) -> dict:
    """Reduce screening records to PRISMA flow counts.

    Returns per-stage totals plus a breakdown of screened-out records by reason.
    ``screened`` = included + screened_out (the papers the relevance gate ruled
    on); ``unverified`` is the subset of included kept without a clear verdict.
    """
    by_stage = {stage: 0 for stage in STAGES}
    excluded_by_reason: dict[str, int] = {}
    unverified = 0
    for rec in records:
        stage = rec.get("stage")
        if stage in by_stage:
            by_stage[stage] += 1
        if stage == STAGE_INCLUDED and rec.get("unverified"):
            unverified += 1
        if stage == STAGE_SCREENED_OUT:
            reason = (rec.get("reason") or "unspecified").strip() or "unspecified"
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
    by_stage["unverified"] = unverified
    by_stage["screened"] = by_stage[STAGE_INCLUDED] + by_stage[STAGE_SCREENED_OUT]
    by_stage["excluded_by_reason"] = excluded_by_reason
    return by_stage


def render_prisma(cfg, cluster: str) -> str:
    """Human-readable PRISMA screening summary for one cluster."""
    records = read_screening_log(cfg, cluster=cluster)
    counts = prisma_counts(records)
    if not records:
        return (
            f"PRISMA screening — cluster '{cluster}'\n"
            "  (no screening records yet; run an ingest with the no-LLM fit "
            "gate to populate the log)"
        )
    lines = [
        f"PRISMA screening — cluster '{cluster}'",
        f"  Identified (database search):  {counts[STAGE_IDENTIFIED]}",
        f"  Removed as duplicates:         {counts[STAGE_DEDUPED]}",
        f"  Screened (relevance gate):     {counts['screened']}",
        f"    -> Included:                 {counts[STAGE_INCLUDED]}",
        f"       (of which unverified:     {counts['unverified']})",
        f"    -> Excluded (screened out):  {counts[STAGE_SCREENED_OUT]}",
    ]
    for reason, n in sorted(
        counts["excluded_by_reason"].items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"         {n:4d}  {reason}")
    return "\n".join(lines)
