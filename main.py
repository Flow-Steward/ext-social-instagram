#!/usr/bin/env python3
"""Minimal subprocess scaffold for the behavioral RED checkpoint.

Provider behavior is intentionally absent until the first assertion is observed RED.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from flowsteward_extension_sdk import runtime_context_from_payload


def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    runtime_context_from_payload(payload)
    message = "Instagram provider behavior is not implemented."
    return {
        "ok": False,
        "error_code": "not_implemented",
        "error": message,
        "result": {},
        "events": [],
        "commands": [],
        "reload": [],
        "errors": [{"code": "not_implemented", "message": message}],
    }


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    response = handle_request(payload)
    print(json.dumps(response))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
