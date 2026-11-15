#!/usr/bin/env python3
"""Exercise the offline subprocess handshake without credentials or provider I/O."""

import json

# Fixed local interpreter/entrypoint; no shell or user-supplied argv.
import subprocess  # nosec B404
import sys
from pathlib import Path


def main() -> int:
    payload = {
        "contract_version": "extension_host_v2",
        "mode": "handshake",
        "runtime_context": {
            "account_id": "",
            "project_id": "",
            "group_id": None,
            "for_each_id": None,
            "step_id": None,
            "step_execution_id": None,
        },
    }
    healthy = False
    try:
        # Fixed script in this package; no shell or user-supplied argv.
        process = subprocess.run(  # nosec B603
            [sys.executable, str(Path(__file__).resolve().parent / "main.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        response = json.loads(process.stdout)
        healthy = (
            process.returncode == 0
            and response.get("ok") is True
            and response.get("result")
            == {
                "contract_version": "extension_host_v2",
                "execution_scope_fields": [
                    "group_id",
                    "for_each_id",
                    "step_id",
                    "step_execution_id",
                ],
            }
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        healthy = False
    print(json.dumps({"status": "healthy" if healthy else "failed"}))
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
