"""Smoke tests for the empty-implementation repo (WP-001, gate G0)."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import urllib.request

import pytest

import doccode


def test_nfr_3_import_doccode_loads_no_heavy_dependency() -> None:
    code = (
        "import sys, doccode\n"
        "heavy = [m for m in ('anthropic', 'weasyprint', 'pydantic') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_nfr_4_package_is_typed_and_versioned() -> None:
    assert isinstance(doccode.__version__, str)
    parts = doccode.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_nfr_6_socket_connect_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="NFR-6"):
            sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()


def test_nfr_6_dns_and_http_are_blocked() -> None:
    with pytest.raises(RuntimeError, match="NFR-6"):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(RuntimeError, match="NFR-6"):
        socket.create_connection(("example.com", 443))
    with pytest.raises(Exception, match="NFR-6"):
        urllib.request.urlopen("http://example.com", timeout=1)


def test_nfr_6_asyncio_still_works_under_the_block() -> None:
    async def main() -> int:
        await asyncio.sleep(0)
        return 42

    assert asyncio.run(main()) == 42
