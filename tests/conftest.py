from __future__ import annotations

import io
import json
import socket
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOCAL_HOSTS:
            raise RuntimeError(f"network access is disabled in tests (tried {address!r})")

    def connect(self, address):
        guard(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        guard(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


def reply(status: int = 200, body: Any = b"", headers: dict[str, str] | None = None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
    elif isinstance(body, str):
        body = body.encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
    return status, headers or {}, body


class FakeTransport(BaseAdapter):
    def __init__(self):
        super().__init__()
        self.routes: dict[str, Any] = {}
        self.requests: list[requests.PreparedRequest] = []

    def add(self, url: str, *responses: Any) -> None:
        self.routes[url] = list(responses) if len(responses) != 1 or not callable(responses[0]) else responses[0]

    def calls_to(self, url: str) -> list[requests.PreparedRequest]:
        return [r for r in self.requests if r.url == url or r.url.split("?")[0] == url]

    def _lookup(self, url: str):
        if url in self.routes:
            return self.routes[url]
        parts = urlsplit(url)
        bare = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        if bare in self.routes:
            return self.routes[bare]
        if parts.path == "/robots.txt":
            return [reply(404, "")]
        return [reply(404, "not found")]

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.requests.append(request)
        route = self._lookup(request.url)
        if callable(route):
            result = route(request)
        else:
            result = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(result, Exception):
            raise result
        status, headers, body = result
        response = requests.Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict(headers)
        response.raw = io.BytesIO(body)
        response.url = request.url
        response.request = request
        response.encoding = get_encoding_from_headers(response.headers)
        response.reason = "OK" if status < 400 else "ERROR"
        return response

    def close(self):
        pass


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def http(transport) -> requests.Session:
    session = requests.Session()
    session.mount("https://", transport)
    session.mount("http://", transport)
    return session


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_reply() -> Callable[..., tuple]:
    return reply
