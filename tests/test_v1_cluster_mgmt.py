"""Wave 2: cluster management commands - paper find/add-to-cluster and coverage."""
from __future__ import annotations

from types import SimpleNamespace


def _make_cfg(tmp_path):
    """Create a minimal cfg with raw/ directory."""
    raw = tmp_path / "raw"
    raw.mkdir()
    return SimpleNamespace(
        raw=raw,
        clusters_file=tmp_path / "clusters.yaml",
        research_hub_dir=tmp_path / ".research_hub",
        root=tmp_path,
    )


def _write_paper(
    cluster_dir,
    stem,
    title,
    doi="",
    author="",
    summarize_status="done",
):
    cluster_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = (
        "---\n"
        f"title: {title}\n"
        f"doi: {doi}\n"
        f"author: {author}\n"
        f"summarize_status: {summarize_status}\n"
        "---\n\n"
        f"# {title}\n"
    )
    (cluster_dir / f"{stem}.md").write_text(frontmatter, encoding="utf-8")


def test_compute_coverage_empty_cluster(tmp_path):
    """compute_coverage handles clusters with no paper directory."""
    from research_hub.clusters import compute_coverage

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: mytest\n    name: My Test\n",
        encoding="utf-8",
    )

    rows = compute_coverage(cfg)

    assert len(rows) == 1
    assert rows[0].slug == "mytest"
    assert rows[0].paper_count == 0
    assert rows[0].coverage_score == 0


def test_compute_coverage_with_papers(tmp_path):
    """compute_coverage counts papers and pending summaries correctly."""
    from research_hub.clusters import compute_coverage

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: floods\n    name: Floods\n",
        encoding="utf-8",
    )

    cluster_dir = cfg.raw / "floods"
    _write_paper(cluster_dir, "paper1", "Flood risk", doi="10.1/a", summarize_status="done")
    _write_paper(cluster_dir, "paper2", "Flood model", doi="10.1/b", summarize_status="pending")
    _write_paper(cluster_dir, "paper3", "Flood adapt", doi="10.1/c", summarize_status="done")

    rows = compute_coverage(cfg)

    assert len(rows) == 1
    row = rows[0]
    assert row.paper_count == 3
    assert row.pending_summary == 1
    assert 0 < row.coverage_score <= 100


def test_compute_coverage_skips_archived(tmp_path):
    """compute_coverage skips archived clusters."""
    from research_hub.clusters import compute_coverage

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n"
        "  - slug: active\n"
        "    name: Active\n"
        "  - slug: old\n"
        "    name: Old\n"
        "    status: archived\n",
        encoding="utf-8",
    )

    rows = compute_coverage(cfg)
    slugs = [row.slug for row in rows]

    assert "active" in slugs
    assert "old" not in slugs


def test_paper_find_by_title(tmp_path, capsys):
    """paper find searches papers by title substring."""
    from research_hub.cli import _cmd_paper_find

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: floods\n    name: Floods\n",
        encoding="utf-8",
    )
    cluster_dir = cfg.raw / "floods"
    _write_paper(cluster_dir, "flood-risk-2023", "Flood Risk Analysis 2023", doi="10.1/a")
    _write_paper(cluster_dir, "ml-methods", "Machine Learning Methods", doi="10.1/b")

    args = SimpleNamespace(query="flood", cluster=None, by="title")
    _cmd_paper_find(cfg, args)

    out = capsys.readouterr().out
    assert "flood-risk-2023" in out
    assert "ml-methods" not in out


def test_paper_find_no_results(tmp_path, capsys):
    """paper find prints no results when nothing matches."""
    from research_hub.cli import _cmd_paper_find

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: floods\n    name: Floods\n",
        encoding="utf-8",
    )
    _write_paper(cfg.raw / "floods", "paper1", "Flood study", doi="10.1/a")

    args = SimpleNamespace(query="unrelated-term-xyz", cluster=None, by="any")
    _cmd_paper_find(cfg, args)

    out = capsys.readouterr().out
    assert "No papers matched" in out or "0" in out


def test_paper_add_to_cluster_by_doi(tmp_path, capsys):
    """paper add-to-cluster adds topic_cluster to frontmatter by DOI match."""
    from research_hub.cli import _cmd_paper_add_to_cluster

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n"
        "  - slug: floods\n"
        "    name: Floods\n"
        "  - slug: llm\n"
        "    name: LLMs\n",
        encoding="utf-8",
    )
    cluster_dir = cfg.raw / "floods"
    _write_paper(cluster_dir, "flood-paper", "Flood study", doi="10.1234/test")

    args = SimpleNamespace(slug_or_doi="10.1234/test", target_cluster="llm", dry_run=False)
    _cmd_paper_add_to_cluster(cfg, args)

    content = (cluster_dir / "flood-paper.md").read_text(encoding="utf-8")
    assert "topic_cluster" in content
    assert "llm" in content


def test_paper_add_to_cluster_dry_run(tmp_path, capsys):
    """paper add-to-cluster --dry-run does not modify the file."""
    from research_hub.cli import _cmd_paper_add_to_cluster

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: floods\n    name: Floods\n  - slug: llm\n    name: LLMs\n",
        encoding="utf-8",
    )
    cluster_dir = cfg.raw / "floods"
    _write_paper(cluster_dir, "flood-paper", "Flood study", doi="10.1234/test")
    paper_path = cluster_dir / "flood-paper.md"
    original = paper_path.read_text(encoding="utf-8")

    args = SimpleNamespace(slug_or_doi="10.1234/test", target_cluster="llm", dry_run=True)
    _cmd_paper_add_to_cluster(cfg, args)

    assert paper_path.read_text(encoding="utf-8") == original


def test_paper_add_to_cluster_already_present(tmp_path, capsys):
    """paper add-to-cluster does nothing if already in target cluster."""
    from research_hub.cli import _cmd_paper_add_to_cluster

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: floods\n    name: Floods\n  - slug: llm\n    name: LLMs\n",
        encoding="utf-8",
    )
    cluster_dir = cfg.raw / "floods"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    paper_path = cluster_dir / "paper.md"
    paper_path.write_text(
        "---\n"
        "title: Test\n"
        "doi: 10.1/x\n"
        "topic_cluster:\n"
        "  - llm\n"
        "---\n\n"
        "Content.\n",
        encoding="utf-8",
    )
    original = paper_path.read_text(encoding="utf-8")

    args = SimpleNamespace(slug_or_doi="10.1/x", target_cluster="llm", dry_run=False)
    _cmd_paper_add_to_cluster(cfg, args)

    assert paper_path.read_text(encoding="utf-8") == original
    out = capsys.readouterr().out
    assert "already" in out.lower() or "no change" in out.lower()


# ---------------------------------------------------------------------------
# Helper unit tests (S3 from code-review)
# ---------------------------------------------------------------------------


def test_update_paper_frontmatter_no_existing_frontmatter(tmp_path: Path) -> None:
    """_update_paper_frontmatter prepends fresh frontmatter when none exists."""
    from research_hub.cli import _update_paper_frontmatter, _read_paper_frontmatter

    body = "# Just a title\n\nSome content.\n"
    result = _update_paper_frontmatter(body, {"topic_cluster": ["floods"]})
    assert result.startswith("---\n")
    fm = _read_paper_frontmatter(result)
    assert fm.get("topic_cluster") == ["floods"]
    assert "Just a title" in result
    assert "Some content." in result


def test_paper_add_to_cluster_invalid_slug(tmp_path: Path, capsys) -> None:
    """paper add-to-cluster rejects invalid cluster names (spaces, uppercase)."""
    from research_hub.cli import _cmd_paper_add_to_cluster

    cfg = _make_cfg(tmp_path)
    cluster_dir = cfg.raw / "floods"
    _write_paper(cluster_dir, "test-paper", "Test", doi="10.1/t")

    args = SimpleNamespace(slug_or_doi="10.1/t", target_cluster="Invalid Cluster!", dry_run=False)
    _cmd_paper_add_to_cluster(cfg, args)

    # File must be unchanged
    content = (cluster_dir / "test-paper.md").read_text(encoding="utf-8")
    assert "topic_cluster" not in content


def test_compute_coverage_latest_mtime_populated(tmp_path: Path) -> None:
    """compute_coverage populates latest_mtime with the max paper file mtime."""
    from research_hub.clusters import compute_coverage

    cfg = _make_cfg(tmp_path)
    cfg.clusters_file.write_text(
        "clusters:\n  - slug: floods\n    name: Floods\n",
        encoding="utf-8",
    )
    cluster_dir = cfg.raw / "floods"
    _write_paper(cluster_dir, "paper1", "Flood study", doi="10.1/a")

    rows = compute_coverage(cfg)
    assert len(rows) == 1
    assert rows[0].latest_mtime > 0.0
