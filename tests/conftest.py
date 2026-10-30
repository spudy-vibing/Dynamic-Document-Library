"""Shared test fixtures (WP-001; owned by the Architect from Wave 1, CR only).

NFR-6: no test may make a network call. An autouse fixture replaces the socket entry points
that reach a network (connect, DNS lookup, sendto) with a function that raises
:class:`NetworkBlockedError`. ``AF_UNIX`` sockets and :func:`socket.socketpair` stay allowed,
because asyncio uses a socket pair for its own wake-up channel (on Windows over loopback).

Tests marked ``@pytest.mark.live`` (NFR-10) are skipped unless ``DOCCODE_LIVE_TESTS=1``; when
they do run, the network is not blocked for them. Do not disable this fixture.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Iterator
from typing import Any

import pytest

LIVE_ENV = "DOCCODE_LIVE_TESTS"


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to reach the network (NFR-6)."""


_state = threading.local()


def _allowed() -> bool:
    return bool(getattr(_state, "in_socketpair", False))


def _is_unix(sock: socket.socket) -> bool:
    return getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX


def _refuse(what: str, target: object) -> NetworkBlockedError:
    return NetworkBlockedError(
        f"network access blocked in tests (NFR-6): {what} {target!r}. "
        f"Use MockProvider or a stub; live tests need @pytest.mark.live and {LIVE_ENV}=1."
    )


@pytest.fixture(autouse=True)
def _block_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("live") is not None:
        yield
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_socketpair = socket.socketpair

    def connect(self: socket.socket, address: Any) -> None:
        if _allowed() or _is_unix(self):
            return real_connect(self, address)
        raise _refuse("connect to", address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if _allowed() or _is_unix(self):
            return real_connect_ex(self, address)
        raise _refuse("connect_ex to", address)

    def sendto(self: socket.socket, *args: Any) -> int:
        if _is_unix(self):
            return real_sendto(self, *args)
        raise _refuse("sendto", args[-1] if args else None)

    def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        _state.in_socketpair = True
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            _state.in_socketpair = False

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        raise _refuse("DNS lookup of", host)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        raise _refuse("create_connection to", address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(LIVE_ENV) == "1":
        return
    skip_live = pytest.mark.skip(reason=f"live test: set {LIVE_ENV}=1 to run (NFR-10)")
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip_live)
