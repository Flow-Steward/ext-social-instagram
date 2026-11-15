"""Public SDK envelopes and a fake transport; never contact a provider."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main  # noqa: E402
from handlers import transport  # noqa: E402

ACCOUNT = "10001"
OTHER_ACCOUNT = "20002"
MEDIA = "30003"
COMMENT = "40004"
CONTAINER = "70007"
TOKEN = "fixture-instagram-token"  # pragma: allowlist secret
APP_SECRET = "fixture-app-secret"  # pragma: allowlist secret
IMAGE = "https://images.example.org/photo.jpg"


def envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": "extension_host_v2",
        "runtime_context": {
            "account_id": "account-a",
            "project_id": "project-a",
            "group_id": None,
            "for_each_id": None,
            "step_id": None,
            "step_execution_id": None,
        },
        **value,
    }


def action(name: str, values: dict[str, Any] | None = None, **config: Any) -> dict[str, Any]:
    return envelope(
        {
            "mode": "action",
            "action": {
                "action_id": name,
                "context": {"account_id": "account-a", "project_id": "project-a"},
                "input": values or {},
                "target": {
                    "connection": {
                        "connection_id": "connection-account-a",
                        "connection_type": "instagram_account",
                        "project_id": "project-a",
                        "account_id": "account-a",
                        "config": {
                            "ig_user_id": ACCOUNT,
                            "app_id": "50005",
                            "graph_api_version": "v27.0",
                            **config,
                        },
                        "secrets": {"access_token": TOKEN, "app_secret": APP_SECRET},
                    }
                },
            },
        }
    )


def with_receipt(
    request: dict[str, Any],
    *,
    status: str = "pending",
    account: str = ACCOUNT,
    connection: str = "connection-account-a",
) -> dict[str, Any]:
    request["action"]["target"]["project_state"] = {
        "items": [
            {
                "namespace": "publishing",
                "state_key": "containers",
                "revision": 7,
                "connection_id": "connection-account-a",
                "value": {
                    "containers": {
                        CONTAINER: {
                            "ig_user_id": account,
                            "connection_id": connection,
                            "container_id": CONTAINER,
                            "status": status,
                        },
                        "80008": {
                            "ig_user_id": ACCOUNT,
                            "connection_id": "connection-account-a",
                            "container_id": "80008",
                            "status": "pending",
                        },
                    }
                },
            }
        ]
    }
    return request


def comment_change(**overrides: Any) -> dict[str, Any]:
    value = {
        "id": COMMENT,
        "from": {"id": "60006", "username": "reader"},
        "media": {"id": MEDIA, "media_product_type": "FEED"},
        "text": "A useful question",
    }
    value.update(overrides)
    return {"field": "comments", "value": value}


def delivery(
    changes: list[dict[str, Any]] | None = None, *, account: str = ACCOUNT
) -> dict[str, Any]:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account,
                "time": 1794690001,
                "changes": changes if changes is not None else [comment_change()],
            }
        ],
    }


def ingress(body: dict[str, Any] | None = None, **config: Any) -> dict[str, Any]:
    request = action("ingress_event")
    request["action"]["input"] = {
        "callback": {
            "authenticated": True,
            "extension_id": "flowsteward.instagram",
            "callback_id": "comments",
            "project_id": "project-a",
            "connection_id": "connection-anchor",
        },
        "request": {
            "method": "POST",
            "json": body if body is not None else delivery(),
            "provider": {
                "provider_key": "flowsteward.instagram",
                "project_id": "project-a",
                "connection_id": "connection-anchor",
                "provider_config": {
                    "ig_user_id": ACCOUNT,
                    "app_id": "50005",
                    "graph_api_version": "v27.0",
                    **config,
                },
            },
        },
    }
    request["action"]["target"] = {}
    return request


def invoke(request: dict[str, Any]) -> dict[str, Any]:
    return main.handle_request(copy.deepcopy(request))


class FakeGraph:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    def reply(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.responses.append((status, headers or {}, raw))

    def fail(self, error: Exception) -> None:
        self.responses.append(error)

    def __call__(self, method, url, *, headers, body, timeout, max_bytes):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected provider request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def form_body(self, index: int = -1) -> dict[str, list[str]]:
        return parse_qs(self.requests[index]["body"].decode("utf-8"), keep_blank_values=True)

    def ready(self) -> None:
        self.reply(200, {"id": CONTAINER, "status_code": "FINISHED"})

    def published(self) -> None:
        self.reply(200, {"id": MEDIA})
        self.reply(200, {"id": MEDIA, "permalink": "https://www.instagram.com/p/example/"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No live network in extension tests")

    monkeypatch.setattr(transport, "_http_exchange", blocked)
    monkeypatch.setattr("socket.create_connection", blocked)


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(transport, "_http_exchange", fake)
    return fake
