#!/usr/bin/env python3
"""Public SDK subprocess adapter for Instagram operations and signed comments."""

from __future__ import annotations

import json
import sys
from typing import Any

from flowsteward_extension_sdk import (
    connection_from_payload,
    handshake_result,
    runtime_context_from_payload,
)

from handlers.configuration import configured
from handlers.ingress import normalize
from handlers.operations import OPERATIONS, WRITES, execute
from handlers.responses import ProviderFailure, WriteBoundary, failure, success

MAX_INPUT_BYTES = 2 * 1024 * 1024


def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    operation = ""
    boundary = WriteBoundary()
    try:
        context = runtime_context_from_payload(payload)
    except (ValueError, TypeError):
        return failure(ProviderFailure("invalid_host_envelope"))
    try:
        mode = payload.get("mode")
        if mode == "handshake":
            return success(handshake_result())
        if mode == "query":
            query = payload.get("query")
            if not isinstance(query, dict):
                raise ProviderFailure("invalid_payload")
            if query.get("query_id") == "provider_health":
                return success(
                    {
                        "status": "select_connection",
                        "configured": False,
                        "webhook_configured": False,
                        "missing_fields": ["selected_connection"],
                    }
                )
            if query.get("query_id") != "connection_health":
                raise ProviderFailure("invalid_payload")
            connection = connection_from_payload(payload)
            missing = [
                key
                for key in ("ig_user_id", "app_id", "graph_api_version")
                if not connection or not connection.config.get(key)
            ]
            if not connection or not connection.secrets.get("access_token"):
                missing.append("access_token")
            try:
                configured(connection)
            except ProviderFailure:
                return success(
                    {
                        "status": "not_configured",
                        "configured": False,
                        "webhook_configured": False,
                        "missing_fields": missing,
                    }
                )
            return success(
                {
                    "status": "configured",
                    "configured": True,
                    "webhook_configured": bool(connection and connection.secrets.get("app_secret")),
                    "missing_fields": [],
                    "checks": [
                        {
                            "name": "Publishing configuration",
                            "status": "healthy",
                            "detail": "Required outbound configuration is present; account identity and permissions are verified separately.",
                        },
                        {
                            "name": "Receiving comments",
                            "status": "healthy"
                            if connection and connection.secrets.get("app_secret")
                            else "warning",
                            "detail": "Webhook signing secret is present; configure the callback below."
                            if connection and connection.secrets.get("app_secret")
                            else "Add the optional app secret to receive signed comments; outbound publishing does not require it.",
                        },
                    ],
                }
            )
        action = payload.get("action")
        if mode != "action" or not isinstance(action, dict):
            raise ProviderFailure("invalid_payload")
        raw_operation = action.get("action_id")
        if not isinstance(raw_operation, str):
            raise ProviderFailure("invalid_payload")
        operation = raw_operation
        values = action.get("input", {})
        target = action.get("target", {})
        if not isinstance(values, dict) or not isinstance(target, dict):
            raise ProviderFailure("invalid_payload")
        if operation == "ingress_event":
            return normalize(values, context)
        if operation not in OPERATIONS:
            raise ProviderFailure("invalid_payload")
        return execute(operation, values, connection_from_payload(payload), target, boundary)
    except ProviderFailure as error:
        return failure(
            boundary.classify(error), write=operation in WRITES, no_effect=boundary.no_effect
        )
    except Exception:
        return failure(
            boundary.classify(ProviderFailure("internal_error")),
            write=operation in WRITES,
            no_effect=boundary.no_effect,
        )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Oversize invocation")
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Invalid envelope")
        response = handle_request(payload)
    except (ValueError, TypeError):
        response = failure(ProviderFailure("invalid_host_envelope"))
    print(json.dumps(response))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
