"""Test setup: mocks only, no network, no real .env."""
from __future__ import annotations

import socket

import pytest

from backend.clock import FakeClock, ist_ts
from backend.config import Settings
from backend.storage import Storage


class NetworkBlocked(RuntimeError):
    pass


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _host(address) -> str:
    return address[0] if isinstance(address, tuple) else str(address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-loopback connection (api.meta.ai, graph.facebook.com, anywhere) fails the test.

    Tests can't spend API credits or send WhatsApp messages, even by mistake. Loopback stays
    open because asyncio's event loop uses a local socket pair on Windows.
    """
    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex
    orig_create, orig_getaddrinfo = socket.create_connection, socket.getaddrinfo

    def guard(host: str) -> None:
        if host not in LOOPBACK:
            raise NetworkBlocked(f"network access to {host!r} is disabled in tests")

    def connect(self, address):
        guard(_host(address))
        return orig_connect(self, address)

    def connect_ex(self, address):
        guard(_host(address))
        return orig_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        guard(_host(address))
        return orig_create(address, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        guard(str(host))
        return orig_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None: never read the developer's real .env in tests.
    return Settings(_env_file=None, use_mocks=True, data_dir=tmp_path / "data")


@pytest.fixture
def storage() -> Storage:
    db = Storage(":memory:")
    db.migrate()
    yield db
    db.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(ist_ts(2026, 10, 29, 21, 0))
