"""YAML package declarations inspected independently of private host modules."""

from __future__ import annotations

import ast
import hashlib
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VISIBLE = {
    "publish_image",
    "publish_carousel",
    "get_publish_status",
    "publish_container",
    "list_posts",
    "get_post",
    "list_comments",
    "list_replies",
    "reply_to_comment",
}
WRITES = {"publish_image", "publish_carousel", "publish_container", "reply_to_comment"}


def load(name):
    return yaml.safe_load((ROOT / name).read_text())


def test_identity_and_host_contract_match_descriptor():
    manifest = load("extension.yaml")
    descriptor = load("extension-descriptor.yaml")
    assert manifest["extension_id"] == descriptor["extension_id"] == "flowsteward.instagram"
    assert manifest["version"] == descriptor["release_version"] == "0.1.0"
    assert manifest["kind"] == "tool_provider"
    assert (
        manifest["runtime"]["contract_version"]
        == descriptor["runtime"]["contract_version"]
        == "extension_host_v2"
    )
    assert (
        "flowsteward-extension-sdk>=0.5.0"
        in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    )
    assert not list(ROOT.rglob("*.json"))


def test_visible_actions_have_same_id_v2_operations_and_step_forms():
    actions = load("ui/actions/actions.yaml")["actions"]
    operations = load("contracts/operation_manifest.yaml")["operations"]
    forms = load("contracts/step_ui_manifest.yaml")["forms"]
    assert {row["action_id"] for row in actions if row.get("workflow_visible")} == VISIBLE
    assert {row["operation_id"] for row in operations if row.get("workflow_visible")} == VISIBLE
    assert {row["operation_id"] for row in forms} == VISIBLE
    for row in operations:
        assert row["connection_type_ids"] == ["instagram_account"]
        assert any(value["value_type"] == "connection_ref" for value in row["inputs"])
    assert all(
        not row.get("workflow_visible")
        for row in actions
        if row["action_id"] in {"verify_connection", "ingress_event", "save_config", "save_secrets"}
    )


def test_only_writes_require_external_effect_idempotency_and_test_suppression():
    effects = load("extension.yaml")["external_effects"]
    assert {row["operation_id"] for row in effects} == WRITES
    assert all(
        row["idempotency"] == "required" and row["test_mode_behavior"] == "suppress"
        for row in effects
    )


def test_generic_signature_profile_and_page_trigger_filter_are_declared():
    manifest = load("extension.yaml")
    callback = manifest["callbacks"][0]
    assert callback["connection_scoped"] is True
    assert set(callback["methods"]) == {"GET", "POST"}
    assert callback["auth"] == {
        "type": "raw_body_hmac_sha256",
        "signature_header": "X-Hub-Signature-256",
        "signature_prefix": "sha256=",
        "secret_name": "app_secret",
        "verification": {
            "token_parameter": "hub.verify_token",
            "challenge_parameter": "hub.challenge",
            "required_parameters": {"hub.mode": "subscribe"},
        },
    }
    trigger = manifest["workflow_triggers"][0]
    assert trigger["event_name"] == "instagram.comment.created"
    assert trigger["provider_key"] == "flowsteward.instagram"
    assert "ig_user_id" in trigger["config_schema"]["properties"]


def test_connection_ui_explicitly_saves_config_and_secret_fields():
    form = load("ui/components/connection_form.yaml")
    assert form["type"] == "connection_form"
    assert form["data"]["connection_type"] == "instagram_account"
    assert set(form["data"]["config_fields"]) == {
        "ig_user_id",
        "app_id",
        "graph_api_version",
        "webhook_account_ids",
    }
    assert set(form["data"]["secret_fields"]) == {"access_token", "app_secret"}
    fields = {row["path"]: row for row in form["fields"]}
    assert fields["graph_api_version"]["required"] is True
    assert "graph_api_version" not in form.get("defaults", {})
    assert fields["access_token"]["widget"] == fields["app_secret"]["widget"] == "secret"


def test_artwork_is_exact_official_source_with_bundled_provenance():
    digest = hashlib.sha256((ROOT / "icon.png").read_bytes()).hexdigest()
    assert digest == "8878662ce6afc4a995bd80c67652a72642c9564b40cfc5ac94a802d7e0f8b6b1"
    provenance = (ROOT / "BRANDING.md").read_text()
    assert digest in provenance
    assert (
        "https://www.instagram.com/static/images/ico/favicon-192.png/68d99ba29cc8.png" in provenance
    )


def test_runtime_and_non_host_tests_do_not_import_core_or_sibling_extensions():
    forbidden = ("core", "extensions", "main_ingress", "main_send", "main_state")
    for file in ROOT.rglob("*.py"):
        file_forbidden = forbidden
        if file == ROOT / "tests/test_host_contracts.py":
            file_forbidden = tuple(name for name in forbidden if name != "core")
        for node in ast.walk(ast.parse(file.read_text())):
            names = (
                [row.name for row in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert all(name.split(".")[0] not in file_forbidden for name in names), file


def test_app_secret_is_optional_for_connection_but_remains_callback_signing_secret():
    types = load("contracts/connection_types.yaml")["connection_types"][0]
    assert types["secret_schema"]["required"] == ["access_token"]
    assert load("schemas/secrets.schema.yaml")["required"] == ["access_token"]
    form = load("ui/components/connection_form.yaml")
    assert form["data"]["required_secret_fields"] == ["access_token"]
    assert not next(row for row in form["fields"] if row["path"] == "app_secret").get(
        "required", False
    )
    assert load("extension.yaml")["callbacks"][0]["auth"]["secret_name"] == "app_secret"


def test_declared_resume_state_is_nonsecret_and_connection_scoped_by_host():
    declaration = load("extension.yaml")["project_state"]
    assert declaration["reads"] == [
        {
            "namespace": "publishing",
            "state_key_from_input": "container_id",
            "state_key_prefix": "container:",
        }
    ]
    assert declaration["writes"] == [{"namespace": "publishing", "state_key": "*"}]


def test_image_contract_limits_match_current_media_reference():
    actions = {row["action_id"]: row for row in load("ui/actions/actions.yaml")["actions"]}
    image = {row["name"]: row for row in actions["publish_image"]["parameters"]}
    carousel = {row["name"]: row for row in actions["publish_carousel"]["parameters"]}
    assert image["caption"]["maxLength"] == carousel["caption"]["maxLength"] == 2200
    assert image["alt_text"]["maxLength"] == 1000
    assert carousel["image_urls"]["minItems"] == 2
    assert carousel["image_urls"]["maxItems"] == 20
    trigger = load("extension.yaml")["workflow_triggers"][0]
    assert set(trigger["config_schema"]["properties"]) == {"ig_user_id", "media_id"}


def test_manual_webhook_setup_uses_selected_account_without_blocking_first_connection():
    pages = load("ui/ui_manifest.yaml")["pages"]
    assert {row["ref"] for row in pages} == {
        "pages/overview.yaml",
        "pages/connection.yaml",
        "pages/webhooks.yaml",
    }
    connection_page = load("ui/pages/connection.yaml")
    assert connection_page["project_scoped"] is True
    assert "connection" not in connection_page
    assert {row["ref"] for row in connection_page["components"]} == {
        "../components/connection_form.yaml",
        "../components/setup_help.yaml",
    }
    webhook_page = load("ui/pages/webhooks.yaml")
    assert webhook_page["project_scoped"] is True
    assert webhook_page["connection"] == {"connection_type_ids": ["instagram_account"]}
    assert webhook_page["components"] == [
        {"ref": "../components/connection_health.yaml"},
        {"ref": "../components/callback_setup.yaml"},
    ]
    callback = load("ui/components/callback_setup.yaml")
    assert callback["type"] == "callback_setup"
    assert callback["callback_id"] == load("extension.yaml")["callbacks"][0]["callback_id"]
    assert callback["setup_mode"] == "manual"
    assert "configure_action_id" not in callback
    assert "status_action_id" not in callback
    assert "configure_webhook" not in {
        row["action_id"] for row in load("ui/actions/actions.yaml")["actions"]
    }


def test_overview_can_show_readiness_before_any_account_exists():
    overview = load("ui/pages/overview.yaml")
    assert overview["project_scoped"] is True
    assert "connection" not in overview
    assert overview["components"] == [{"ref": "../components/health_panel.yaml"}]
    health = load("ui/components/health_panel.yaml")
    assert health["type"] == "health_panel"
    assert health["query_id"] == "provider_health"
    query = next(
        row
        for row in load("ui/queries/queries.yaml")["queries"]
        if row["query_id"] == health["query_id"]
    )
    assert "connection" not in query
    assert query["handler"]["mode"] == "extension"
    assert "result.configured" in query["result_fields"]


def test_manual_callbacks_require_current_host_api_floor():
    assert load("extension.yaml")["runtime"]["compatibility"]["platform_min"] == "1.9.0"
    assert load("extension-descriptor.yaml")["runtime"]["compatibility"]["platform_min"] == "1.9.0"
