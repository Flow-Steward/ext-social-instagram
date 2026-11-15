"""Bounded fixed-origin Graph transport over the public SDK pinned HTTP opener."""

from __future__ import annotations

import json
import math
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit

from flowsteward_extension_sdk import Request, open_pinned_url

from handlers.deadline import blocking_deadline
from handlers.responses import ProviderFailure, WriteBoundary

MAX_BYTES = 2 * 1024 * 1024
OPERATION_SECONDS = 25.0


def pinned_exchange(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.instagram.com"
        or parsed.fragment
        or method not in {"GET", "POST"}
        or not math.isfinite(timeout)
        or not 0 < timeout <= 30
        or not 0 < max_bytes <= MAX_BYTES
    ):
        raise ProviderFailure("invalid_payload")
    with blocking_deadline(timeout):
        try:
            response = open_pinned_url(
                Request(url, data=body, headers=headers, method=method), timeout_seconds=timeout
            )
        except HTTPError as error:
            response = error
        try:
            chunks = []
            size = 0
            reader = getattr(response, "read1", None) or response.read
            while True:
                chunk = reader(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise ProviderFailure("response_too_large")
            return (
                int(response.status if response.status is not None else response.code),
                dict(response.headers),
                b"".join(chunks),
            )
        finally:
            response.close()


_http_exchange = pinned_exchange


def redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: redact(item, secrets)
            for key, item in value.items()
            if key.lower() not in {"access_token", "app_secret", "paging", "next"}
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


class GraphClient:
    def __init__(
        self, version: str, token: str, boundary: WriteBoundary, app_secret: str | None = None
    ) -> None:
        self.version = version
        self.token = token
        self.boundary = boundary
        self.secrets = (token, app_secret or "")
        self.deadline = time.monotonic() + OPERATION_SECONDS

    def request(self, method: str, path: str, values: dict[str, Any]) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure("timeout")
        fields = {
            key: json.dumps(value, separators=(",", ":"))
            if isinstance(value, (list, dict, bool))
            else str(value)
            for key, value in values.items()
        }
        url = f"https://graph.instagram.com/{self.version}/{path}"
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        body = None
        if method == "GET" and fields:
            url += "?" + urlencode(fields)
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields).encode("utf-8")
        try:
            if method == "POST":
                self.boundary.attempts += 1
            status, _, raw = _http_exchange(
                method,
                url,
                headers=headers,
                body=body,
                timeout=min(10.0, remaining),
                max_bytes=MAX_BYTES,
            )
            if len(raw) > MAX_BYTES:
                raise ProviderFailure("response_too_large", uncertain=method == "POST")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError) as error:
                raise ProviderFailure("provider_unavailable", uncertain=method == "POST") from error
            if not isinstance(result, dict) or 300 <= status < 400:
                raise ProviderFailure("provider_unavailable", uncertain=method == "POST")
            if status >= 400 or "error" in result:
                detail = result.get("error")
                code = detail.get("code") if isinstance(detail, dict) else None
                if type(code) is not int:
                    raise ProviderFailure("provider_unavailable", uncertain=method == "POST")
                if status == 401 or code == 190:
                    failure = "authentication_failed"
                elif status == 403 or code in {10, 200}:
                    failure = "permission_denied"
                elif status == 429 or code in {4, 17, 32, 613}:
                    failure = "rate_limited"
                elif status == 404:
                    failure = "not_found"
                else:
                    failure = "provider_unavailable"
                # A timed-out POST remains uncertain under the conservative write policy.
                rejection = 400 <= status < 500 and status != 408
                if method == "POST" and rejection:
                    self.boundary.rejections += 1
                raise ProviderFailure(failure, uncertain=method == "POST" and not rejection)
            paging = result.get("paging")
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            cursor = cursors.get("after") if isinstance(cursors, dict) else None
            cleaned = redact(result, self.secrets)
            cleaned.pop("_next_cursor", None)
            if (
                isinstance(cursor, str)
                and len(cursor) <= 2048
                and not any(ord(c) < 32 or ord(c) == 127 for c in cursor)
                and not any(secret and secret in cursor for secret in self.secrets)
            ):
                cleaned["_next_cursor"] = cursor
            return cleaned
        except ProviderFailure as error:
            if method == "POST" and error.uncertain:
                raise ProviderFailure("ambiguous_external_effect", uncertain=True) from error
            raise
        except Exception as error:
            if method == "POST":
                raise ProviderFailure("ambiguous_external_effect", uncertain=True) from error
            is_timeout = isinstance(error, TimeoutError) or isinstance(
                getattr(error, "reason", None), TimeoutError
            )
            raise ProviderFailure("timeout" if is_timeout else "provider_unavailable") from error
