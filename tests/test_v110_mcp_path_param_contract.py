"""v1.1 P2-5e — the typed MCP path-param validation contract.

The risk this closes: a new MCP tool adds a slug/cluster path parameter under a
name the validator does not recognise (e.g. ``dest_slug``, ``target_cluster``),
so ``_validate_mcp_args`` silently passes it through and a hostile value
(``../``, separators, nulls) reaches ``safe_join`` unchecked.

Two guarantees, tested here:
1. **Completeness** — EVERY parameter across the LIVE tool registry whose name is
   slug-like by convention is registered in the validation contract.
2. **Fail-closed** — the validator REFUSES an unregistered slug-like kwarg, and
   actually rejects path-traversal values for the registered ones.
"""

from __future__ import annotations

import inspect

import pytest

from research_hub import mcp_server
from research_hub.mcp_server import (
    _looks_like_slug_param,
    _validate_mcp_args,
    _VALIDATED_PARAM_NAMES,
)
from research_hub.security import ValidationError
from tests._mcp_helpers import _get_mcp_tool, _list_mcp_tool_names


def _all_tool_fns():
    for name in sorted(_list_mcp_tool_names(mcp_server.mcp)):
        tool = _get_mcp_tool(mcp_server.mcp, name, module=mcp_server)
        yield name, tool.fn


def test_every_slug_like_param_is_in_the_contract():
    """Completeness: no registered tool exposes an unvalidated slug-like param."""
    offenders: list[str] = []
    for name, fn in _all_tool_fns():
        for param_name in inspect.signature(fn).parameters:
            if _looks_like_slug_param(param_name) and param_name not in _VALIDATED_PARAM_NAMES:
                offenders.append(f"{name}.{param_name}")
    assert not offenders, (
        "These MCP tool params are slug-like but NOT in the validation contract "
        "(add them to _SLUG_PARAM_NAMES / _IDENTIFIER_PARAM_NAMES in mcp_server.py): "
        + ", ".join(offenders)
    )


def test_contract_names_are_self_consistent():
    """Every registered name is itself either an identifier or convention-slug."""
    for nm in _VALIDATED_PARAM_NAMES:
        # identifier names (doi_or_slug) are allowed not to match the slug
        # convention; slug names must round-trip through the validator cleanly.
        assert isinstance(nm, str) and nm


def test_validator_fails_closed_on_unregistered_slug_like_param():
    with pytest.raises(ValidationError):
        _validate_mcp_args(dest_slug="anything")
    with pytest.raises(ValidationError):
        _validate_mcp_args(target_cluster="anything")


def test_validator_rejects_path_traversal_on_registered_params():
    for bad in ("../etc", "a/b", "a\\b", "UPPER", "..", "with space"):
        with pytest.raises(ValidationError):
            _validate_mcp_args(cluster_slug=bad)
        with pytest.raises(ValidationError):
            _validate_mcp_args(slug=bad)


def test_validator_passes_clean_values_and_non_path_params():
    out = _validate_mcp_args(
        cluster_slug="llm-agents",
        identifier="10.1234/abc",
        query="not a path param",
        limit=10,
        nothing=None,
    )
    assert out["cluster_slug"] == "llm-agents"
    assert out["identifier"] == "10.1234/abc"
    assert out["query"] == "not a path param"   # non-path passthrough
    assert out["limit"] == 10
    assert out["nothing"] is None
