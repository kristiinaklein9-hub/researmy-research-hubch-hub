"""Regression guard for the v1.0.9 structural network fence (P0-5).

Proves the autouse `_network_fence` in conftest.py is active: an unmarked test
cannot open an EXTERNAL socket (so a new leak to a real API fails loudly), while
loopback stays allowed (the local HTTPServer / REST / dashboard tests rely on
binding 127.0.0.1).
"""

from __future__ import annotations

import socket

import pytest
import pytest_socket

_BLOCKED = (pytest_socket.SocketBlockedError, pytest_socket.SocketConnectBlockedError)


def test_network_fence_blocks_external_connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(_BLOCKED):
            # RFC 5737 TEST-NET-1: never routable, so this asserts the fence —
            # not a real connection attempt — even if the guard regressed.
            s.connect(("192.0.2.1", 80))
    finally:
        s.close()


def test_network_fence_allows_loopback_bind():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))  # local HTTPServer tests must keep working
    finally:
        s.close()
