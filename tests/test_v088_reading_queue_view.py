"""v0.88 #9 — base file includes a Reading queue view as the first/default tab."""

from __future__ import annotations

import yaml

from research_hub.obsidian_bases import (
    ClusterBaseInputs,
    _reading_queue_view,
    build_cluster_base,
)


def test_reading_queue_view_filters_unread_for_slug() -> None:
    view = _reading_queue_view("demo-cluster")
    assert view["type"] == "table"
    assert view["name"] == "Reading queue"
    and_block = view["filters"]["and"]
    assert 'topic_cluster == "demo-cluster"' in and_block
    assert 'status == "unread"' in and_block
    assert 'file.ext == "md"' in and_block


def test_reading_queue_groups_by_year_desc() -> None:
    view = _reading_queue_view("demo")
    assert view["groupBy"] == {"property": "year", "direction": "DESC"}


def test_reading_queue_orders_newest_unread_first() -> None:
    view = _reading_queue_view("demo")
    # year + ingested_at order so newest unread papers surface first
    assert view["order"][:2] == ["year", "ingested_at"]


def test_build_cluster_base_places_reading_queue_first() -> None:
    """The default landing tab in Obsidian Bases is the FIRST view in
    the list, so Reading queue must lead."""
    payload = yaml.safe_load(build_cluster_base(ClusterBaseInputs(
        cluster_slug="demo",
        cluster_name="Demo Cluster",
    )))
    views = payload["views"]
    assert views[0]["name"] == "Reading queue"
    assert views[1]["name"] == "Papers"  # second-tier index after the queue


def test_build_cluster_base_emits_all_five_views() -> None:
    payload = yaml.safe_load(build_cluster_base(ClusterBaseInputs(
        cluster_slug="demo",
        cluster_name="Demo Cluster",
    )))
    names = [v["name"] for v in payload["views"]]
    assert names == ["Reading queue", "Papers", "Crystals", "Open Questions", "Recent activity"]
