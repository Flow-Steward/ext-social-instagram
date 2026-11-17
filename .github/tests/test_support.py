"""Support remains reachable without a project and ships in the release bundle."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_support_is_registered_global_safe_and_packaged():
    manifest = yaml.safe_load((ROOT / "extension.yaml").read_text())
    ui = yaml.safe_load((ROOT / manifest["ui_manifest"]).read_text())
    assert {"ref": "pages/support.yaml"} in ui["pages"]
    page = yaml.safe_load((ROOT / "ui/pages/support.yaml").read_text())
    assert page["page_id"] == "support"
    assert page["title"] == "Support"
    assert page["project_scoped"] is False
    assert "connection" not in page
    assert all(component["type"] == "markdown" for component in page["components"])
    body = "\n".join(component["body"] for component in page["components"])
    repository = "https://github.com/Flow-Steward/ext-social-instagram"
    assert f"{repository}/issues/new/choose" in body
    assert f"{repository}/issues" in body
    assert repository in body
    assert manifest["vendor"]["support_url"] == f"{repository}/issues"
    assert "Monitoring → Download diagnostics" in body
    assert "Issues are public" in body
    assert "Never include passwords, API keys, tokens, connection strings or customer data" in body

    spec = importlib.util.spec_from_file_location(
        "support_package_extension", ROOT / ".github/scripts/package_extension.py"
    )
    assert spec is not None and spec.loader is not None
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    shipped = packager.shipped_files()
    assert {"extension.yaml", manifest["ui_manifest"], "ui/pages/support.yaml"} <= set(shipped)
