"""Exercise the actual public-SDK adapter independently of the Graph fake seam."""

from __future__ import annotations

import io
from urllib.error import HTTPError

import pytest
from handlers import transport
from handlers.responses import ProviderFailure


@pytest.mark.parametrize("http_error", [False, True])
def test_real_adapter_calls_sdk_with_finite_timeout_and_closes_bounded_stream(
    monkeypatch, http_error
):
    reads = []

    class Stream(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

        read1 = read

    stream = Stream(b'{"id":"70007"}')
    calls = []

    def opener(request, *, timeout_seconds):
        calls.append((request, timeout_seconds))
        if http_error:
            raise HTTPError("https://graph.instagram.com/v27.0/10001", 503, "busy", {}, stream)
        return stream

    monkeypatch.setattr(transport, "open_pinned_url", opener)
    status, _, body = transport.pinned_exchange(
        "GET",
        "https://graph.instagram.com/v27.0/10001",
        headers={"Authorization": "Bearer fixture"},
        body=None,
        timeout=3.5,
        max_bytes=100,
    )
    assert status == (503 if http_error else 200)
    assert body == b'{"id":"70007"}'
    assert stream.closed
    assert all(0 < size <= 101 for size in reads)
    assert calls[0][0].get_method() == "GET"
    assert calls[0][0].full_url == "https://graph.instagram.com/v27.0/10001"
    assert calls[0][1] == 3.5


def test_real_adapter_closes_stream_when_response_exceeds_limit(monkeypatch):
    class Stream(io.BytesIO):
        status = 200
        headers = {}

    stream = Stream(b"x" * 101)
    monkeypatch.setattr(transport, "open_pinned_url", lambda *a, **kw: stream)
    with pytest.raises(ProviderFailure) as caught:
        transport.pinned_exchange(
            "GET",
            "https://graph.instagram.com/v27.0/10001",
            headers={},
            body=None,
            timeout=2,
            max_bytes=100,
        )
    assert caught.value.code == "response_too_large"
    assert stream.closed
