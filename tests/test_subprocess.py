"""The public stdin/stdout boundary uses the final host contract and failure envelope."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import TOKEN, action, envelope

ROOT = Path(__file__).resolve().parents[1]


def run(payload):
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        timeout=10,
    )


def test_handshake_confirms_final_execution_scope():
    process = run(envelope({"mode": "handshake"}))
    assert process.returncode == 0, process.stdout + process.stderr
    response = json.loads(process.stdout)
    assert response["ok"] is True
    assert response["result"] == {
        "contract_version": "extension_host_v2",
        "execution_scope_fields": ["group_id", "for_each_id", "step_id", "step_execution_id"],
    }


def test_preflight_failure_exits_two_with_canonical_redacted_error():
    request = action(
        "publish_image", {"image_url": "http://invalid.example/a.jpg", "caption": "No"}
    )
    process = run(request)
    assert process.returncode == 2
    response = json.loads(process.stdout)
    assert response["ok"] is False
    assert response["error_code"] == "invalid_payload"
    assert response["errors"][0] == {"code": response["error_code"], "message": response["error"]}
    assert TOKEN not in process.stdout + process.stderr


def test_invalid_host_scope_fails_before_provider_behavior():
    request = action("list_posts")
    request["runtime_context"].pop("for_each_id")
    process = run(request)
    assert process.returncode == 2
    response = json.loads(process.stdout)
    assert response["ok"] is False
    assert response["error_code"] == "invalid_host_envelope"
    assert "Traceback" not in process.stderr


def test_health_is_offline_package_availability():
    process = subprocess.run(
        [sys.executable, str(ROOT / "health.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        timeout=10,
    )
    assert process.returncode == 0
    assert json.loads(process.stdout)["status"] == "healthy"
