"""Exercise the actual public-SDK adapter independently of the Graph fake seam."""

from __future__ import annotations

import io
import json
import signal
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from conftest import ACCOUNT, CONTAINER, IMAGE, action, invoke
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


class VirtualBlockingClock:
    """Charge separate blocking waits without sleeping or opening sockets.

    Model existing stdlib timer and socket timeout interfaces so an absolute
    interrupt or a tightened socket wait can bound the same observable work.
    No production-only test helper or imagined transport API is required.
    """

    def __init__(self, monkeypatch):
        self.now = 0.0
        self.alarm_at = None
        self.interval = 0.0
        self.handlers = {signal.SIGALRM: signal.SIG_DFL}
        monkeypatch.setattr(time, "monotonic", lambda: self.now)
        monkeypatch.setattr(signal, "getitimer", self.getitimer)
        monkeypatch.setattr(signal, "setitimer", self.setitimer)
        monkeypatch.setattr(signal, "signal", self.signal)
        monkeypatch.setattr(
            signal, "getsignal", lambda number: self.handlers.get(number, signal.SIG_DFL)
        )

    def getitimer(self, which):
        assert which == signal.ITIMER_REAL
        return (
            max(0.0, self.alarm_at - self.now) if self.alarm_at is not None else 0.0,
            self.interval,
        )

    def setitimer(self, which, seconds, interval=0.0):
        previous = self.getitimer(which)
        self.alarm_at = self.now + seconds if seconds else None
        self.interval = interval
        return previous

    def signal(self, number, handler):
        previous = self.handlers.get(number, signal.SIG_DFL)
        self.handlers[number] = handler
        return previous

    def wait(self, seconds, socket_timeout):
        duration = min(seconds, socket_timeout)
        if self.alarm_at is not None and self.alarm_at <= self.now + duration:
            self.now = self.alarm_at
            self.alarm_at = self.now + self.interval if self.interval else None
            handler = self.handlers[signal.SIGALRM]
            assert callable(handler), "A runtime alarm must install its interrupt handler"
            handler(signal.SIGALRM, None)
        self.now += duration
        if seconds > socket_timeout:
            raise TimeoutError("virtual socket wait exhausted")


def delayed_sdk_opener(monkeypatch, clock, responses):
    calls = []
    streams = []

    class Stream(io.BytesIO):
        status = 200
        headers = {}

        def __init__(self, raw, timeout, body_wait):
            super().__init__(raw)
            self.timeout = timeout
            self.body_wait = body_wait
            self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self))

        def settimeout(self, seconds):
            self.timeout = seconds

        def gettimeout(self):
            return self.timeout

        def read1(self, size=-1):
            if self.body_wait:
                duration, self.body_wait = self.body_wait, 0
                clock.wait(duration, self.timeout)
            return super().read(size)

    def opener(request, *, timeout_seconds):
        calls.append((request, clock.now, timeout_seconds))
        raw, header_wait, body_wait = responses.pop(0)
        clock.wait(header_wait, timeout_seconds)
        stream = Stream(json.dumps(raw).encode(), timeout_seconds, body_wait)
        streams.append(stream)
        return stream

    monkeypatch.setattr(transport, "open_pinned_url", opener)
    # Restore the actual adapter hidden by the autouse no-network exchange seam.
    monkeypatch.setattr(transport, "_http_exchange", transport.pinned_exchange)
    return calls, streams


def test_real_adapter_budget_includes_headers_and_blocking_body(monkeypatch):
    clock = VirtualBlockingClock(monkeypatch)
    clock.now = 15.0
    calls, streams = delayed_sdk_opener(monkeypatch, clock, [({"id": CONTAINER}, 9, 9)])
    with pytest.raises(TimeoutError):
        transport.pinned_exchange(
            "GET",
            "https://graph.instagram.com/v27.0/70007",
            headers={},
            body=None,
            timeout=10,
            max_bytes=100,
        )
    assert calls[0][1:] == (15.0, 10)
    assert clock.now <= 25.0, "Header and body waits must share the exchange deadline"
    assert all(stream.closed for stream in streams)


def test_real_adapter_known_parent_returns_receipt_before_operation_deadline(monkeypatch):
    clock = VirtualBlockingClock(monkeypatch)
    calls, streams = delayed_sdk_opener(
        monkeypatch,
        clock,
        [
            ({"id": "90001"}, 2, 3),
            ({"id": "90002"}, 2, 3),
            ({"id": CONTAINER}, 2, 3),
            ({"id": CONTAINER, "status_code": "FINISHED"}, 9, 9),
        ],
    )
    response = invoke(
        action("publish_carousel", {"image_urls": [IMAGE, IMAGE], "caption": "Retain parent"})
    )
    assert response["ok"] is True, response
    assert response["result"]["status"] == "pending"
    assert response["result"]["container_id"] == CONTAINER
    assert response["external_effect_status"] == "timeout_unknown"
    update = response["project_state_updates"][0]
    assert update["namespace"] == "publishing"
    assert update["state_key"] == "container:" + CONTAINER
    assert update["value_jsonb"]["ig_user_id"] == ACCOUNT
    assert update["value_jsonb"]["container_id"] == CONTAINER
    assert [call[0].get_method() for call in calls] == ["POST", "POST", "POST", "GET"]
    assert all(0 < timeout <= 25.0 - started for _, started, timeout in calls)
    assert clock.now <= 25.0, "The pending receipt must return before the host's 30-second kill"
    assert all(stream.closed for stream in streams)
