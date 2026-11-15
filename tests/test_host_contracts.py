"""Opt-in actual fs-audit host consumers with fake persistence/queue boundaries.

No credentials, environment or database are accessed. These tests characterize
routing/replay and receipt/effect processing, not SQL atomicity or live execution.
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import (
    ACCOUNT,
    APP_SECRET,
    CONTAINER,
    IMAGE,
    MEDIA,
    OTHER_ACCOUNT,
    ROOT,
    TOKEN,
    action,
    comment_change,
    delivery,
    ingress,
    invoke,
    with_receipt,
)

pytestmark = pytest.mark.skipif(
    not Path("/app/core").is_dir(),
    reason="Opt-in host interoperability requires managed fs-audit /app/core",
)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions import (
        external_effect_replay,
        external_effects,
        operation_service,
        response_envelope,
        project_state_hydration,
    )
    from core.application.management.command_models import ProviderEventIngestInput
    from core.application.management.command_services_event import EventCommandService
    from core.application.management.errors import ServiceForbiddenError
    from core.application.management.scope import ScopeContext
    from core.application.registry.workflow_contract_service import WorkflowInterfaceService
    from core.application.workflows import provider_event_trigger_dispatch

    monkeypatch.setattr(
        WorkflowInterfaceService,
        "interface_for_workflow_id",
        lambda self, _: self.interface_from_definition({"settings": {}, "steps": []}),
    )
    return SimpleNamespace(
        dispatch=provider_event_trigger_dispatch,
        effects=external_effects,
        replay=external_effect_replay,
        service=EventCommandService,
        command=ProviderEventIngestInput,
        scope=ScopeContext,
        forbidden=ServiceForbiddenError,
        operations=operation_service,
        responses=response_envelope,
        hydration=project_state_hydration,
    )


class Persistence:
    def __init__(self):
        self.recorded = []
        self.claims = {}
        self.enqueued = []

    def get_project(self, project_id):
        return {"id": project_id, "account_id": "account-a"}

    def record_domain_event(self, **values):
        self.recorded.append(values)
        return {"event_id": values["event_id"]}

    def claim(self, _store, *, delivery_key, **kwargs):
        if delivery_key in self.claims:
            return {"claimed": False, "job_id": self.claims[delivery_key]}
        self.claims[delivery_key] = "job-" + str(len(self.claims) + 1)
        return {"claimed": True}

    def enqueue(self, **values):
        self.enqueued.append(values)
        return "job-" + str(len(self.enqueued))


@contextmanager
def admitted(*args):
    yield True


def ingestion(host, monkeypatch):
    store = Persistence()
    rows = [
        {
            "trigger_id": name,
            "workflow_id": "workflow-" + name,
            "project_id": project,
            "provider_key": "flowsteward.instagram",
            "trigger_type_id": "instagram_comment_created",
            "event_name": "instagram.comment.created",
            "trigger_config_jsonb": {"ig_user_id": account, "media_id": MEDIA},
        }
        for name, project, account in [
            ("own", "project-a", ACCOUNT),
            ("other", "project-a", OTHER_ACCOUNT),
            ("foreign-project", "project-b", ACCOUNT),
            ("wrong-filter", "project-a", "99999"),
        ]
    ]
    monkeypatch.setattr(
        host.dispatch,
        "list_enabled_provider_triggers_for_provider",
        lambda _, *, provider_key: rows if provider_key == "flowsteward.instagram" else [],
    )
    monkeypatch.setattr(host.dispatch, "provider_trigger_admission", admitted)
    monkeypatch.setattr(
        host.dispatch,
        "resolve_project_run_config_for_workflow_project",
        lambda _store, _workflow, project: (project, {"project_name": "Fixture"}),
    )
    monkeypatch.setattr(host.dispatch, "_claim_provider_webhook_delivery", store.claim)
    monkeypatch.setattr(
        host.dispatch, "_mark_provider_webhook_delivery_accepted", lambda *a, **kw: 1
    )
    monkeypatch.setattr(host.dispatch, "_mark_provider_webhook_delivery_failed", lambda *a, **kw: 1)
    monkeypatch.setattr(host.dispatch, "enqueue_workflow_start", store.enqueue)
    service = host.service(
        get_logger=lambda: store,
        resolve_domain_event_by_ref=lambda *a, **kw: None,
        to_json_obj=lambda value: value if isinstance(value, dict) else {},
        serialize_domain_event_datetime=str,
        enqueue_task=lambda **kw: None,
    )
    scope = host.scope(
        account_id="account-a", project_id="project-a", allowed_project_ids={"project-a"}
    )

    def consume(response, project="project-a"):
        assert response["ok"] and response["events"]
        return service.ingest_provider_events(
            host.command(
                provider_key="flowsteward.instagram",
                project_id=project,
                events=response["events"],
                result=response["result"],
            ),
            scope=scope,
        )

    return store, consume


def test_actual_ingestion_routes_distinct_account_comment_and_claims_replay_once(host, monkeypatch):
    store, consume = ingestion(host, monkeypatch)
    original = invoke(ingress(webhook_account_ids=[ACCOUNT, OTHER_ACCOUNT]))
    retry = ingress(
        delivery([comment_change(text="Changed delivery text")]),
        webhook_account_ids=[ACCOUNT, OTHER_ACCOUNT],
    )
    retry["action"]["input"]["request"]["json"]["entry"][0]["time"] += 100
    again = invoke(retry)
    assert original["events"][0]["source_id"] == again["events"][0]["source_id"]
    consume(original)
    consume(again)
    assert len(store.enqueued) == len(store.claims) == 1
    changed = invoke(
        ingress(
            delivery([comment_change(id="40005")]), webhook_account_ids=[ACCOUNT, OTHER_ACCOUNT]
        )
    )
    other = invoke(
        ingress(delivery(account=OTHER_ACCOUNT), webhook_account_ids=[ACCOUNT, OTHER_ACCOUNT])
    )
    consume(changed)
    consume(other)
    assert len(store.enqueued) == len(store.claims) == 3
    assert [row["selected_workflow_id"] for row in store.enqueued] == [
        "workflow-own",
        "workflow-own",
        "workflow-other",
    ]
    assert {row["source_event_id"] for row in store.enqueued} == {
        response["events"][0]["source_id"] for response in (original, changed, other)
    }
    assert all(
        row["inputs"]["connection_id"] == "connection-anchor" and row["project_id"] == "project-a"
        for row in store.enqueued
    )
    assert len(store.recorded) == 4
    assert all(row["account_id"] == "account-a" for row in store.recorded)


def test_actual_ingestion_refuses_foreign_project_before_any_persistence(host, monkeypatch):
    store, consume = ingestion(host, monkeypatch)
    with pytest.raises(host.forbidden):
        consume(invoke(ingress()), project="project-b")
    assert store.recorded == store.enqueued == []
    assert store.claims == {}


def extension_record():
    manifest = yaml.safe_load((ROOT / "extension.yaml").read_text())
    return {
        **manifest,
        "extension_key": "flowsteward.instagram",
        "extension_install_id": "fixture-install",
        "operation_manifest": yaml.safe_load(
            (ROOT / "contracts/operation_manifest.yaml").read_text()
        )["operations"],
    }


def confirm(host, monkeypatch, response, invocation=None):
    writes = []
    effect = {
        "effect_id": "fixture-effect",
        "account_id": "account-a",
        "extension_id": "flowsteward.instagram",
        "extension_install_id": "fixture-install",
        "operation_id": "publish_image",
        "project_id": "project-a",
        "connection_id": "connection-account-a",
    }

    def persist(_store, **values):
        writes.append(copy.deepcopy(values))
        return {**effect, "status": values["status"]}

    monkeypatch.setattr(
        host.effects.external_effect_store, "get_external_effect", lambda *a, **kw: effect
    )
    monkeypatch.setattr(
        host.effects.external_effect_store, "update_external_effect_status", persist
    )
    monkeypatch.setattr(
        host.effects.external_effect_store, "confirm_external_effect_with_replay", persist
    )
    monkeypatch.setattr(
        host.replay,
        "encrypt_projected_replay",
        lambda *a, **kw: {
            "ciphertext": "fixture-only",
            "binding_hash": "fixture",
            "plaintext_bytes": 100,
        },
    )
    invocation = invocation or {
        "status": "succeeded" if response["ok"] else "failed",
        "response": response,
    }
    confirmed = host.effects.confirm_external_effect_from_invocation(
        object(),
        effect_id="fixture-effect",
        claim_token="fixture-lease",
        account_id="account-a",
        operation_id="publish_image",
        extension=extension_record(),
        invocation=invocation,
        protected_values=(TOKEN,),
        expected_project_id="project-a",
        expected_connection_id="connection-account-a",
    )
    assert len(writes) == 1
    assert writes[0]["claim_token"] == "fixture-lease"
    return confirmed, writes[0]


@pytest.mark.parametrize("outcome", ["ambiguous", "succeeded", "rejected"])
def test_actual_effect_confirmation_consumes_instagram_runtime_metadata(
    host, graph, monkeypatch, outcome
):
    if outcome == "ambiguous":
        graph.fail(TimeoutError("unknown create"))
        expected = "timeout_unknown"
    elif outcome == "succeeded":
        graph.reply(200, {"id": CONTAINER})
        graph.ready()
        graph.published()
        expected = "succeeded"
    else:
        graph.reply(403, {"error": {"code": 200}})
        expected = "failed"
    response = invoke(action("publish_image", {"image_url": IMAGE, "caption": "Once"}))
    confirmed, write = confirm(host, monkeypatch, response)
    assert confirmed["status"] == write["status"] == expected
    if outcome == "succeeded":
        assert write["external_object_id"] == MEDIA
        assert confirmed["__confirmed_replay_invocation"]["response"]["result"]["media_id"] == MEDIA


def test_actual_host_processes_new_parent_pending_receipt_before_confirming_unknown(
    host, graph, monkeypatch
):
    request = action("publish_image", {"image_url": IMAGE, "caption": "Once"})
    graph.reply(200, {"id": CONTAINER})
    graph.ready()
    graph.fail(TimeoutError("may have published"))
    response = invoke(request)
    assert response["ok"] is True and response["external_effect_status"] == "timeout_unknown"
    saved = []

    def put_state(_db, **values):
        saved.append(copy.deepcopy(values))
        return {"revision": 8, **values}

    monkeypatch.setattr(host.responses.extension_project_state, "put_state", put_state)
    store = SimpleNamespace(cursor=lambda: pytest.fail("No SQL in this fixture"))
    service = SimpleNamespace(
        _get_operation_store=lambda: store,
        _event_command_service_factory=None,
        _command_dispatch_service_factory=None,
    )
    context = SimpleNamespace(
        extension=extension_record(),
        extension_id="flowsteward.instagram",
        scope=host.scope(account_id="account-a", project_id="project-a"),
        audit={
            "account_id": "account-a",
            "project_id": "project-a",
            "connection_id": "connection-account-a",
        },
        bl=store,
    )
    invocation = {
        "status": "succeeded",
        "response": response,
        "operation_type": "action",
        "operation_id": "publish_image",
    }
    processed = host.operations.ExtensionOperationService._process_action_response(
        service, context, invocation
    )
    assert processed["status"] == "succeeded"
    assert len(saved) == 1
    assert saved[0]["expected_revision"] is None
    assert saved[0]["connection_id"] == "connection-account-a"
    assert saved[0]["state_key"] == "container:" + CONTAINER
    assert saved[0]["value_jsonb"]["container_id"] == CONTAINER
    confirmed, _ = confirm(host, monkeypatch, response, processed)
    assert confirmed["status"] == "timeout_unknown"
    blocked = host.effects.apply_external_effect_confirmation(processed, confirmed)
    assert blocked["status"] == "failed"
    assert blocked["detail"] == "external_effect_timeout_unknown"
    assert len(saved) == 1  # receipt application happened before the host blocked replay.


class ReceiptPersistence:
    """Fake host put/get boundary preserving its upsert and positive-revision contract."""

    def __init__(self, conflict):
        self.rows = {}
        self.conflict = conflict

    def cursor(self):
        pytest.fail("No actual SQL is allowed in host interoperability tests")

    def put(self, _db, **values):
        key = (values["namespace"], values["state_key"], values.get("connection_id"))
        previous = self.rows.get(key)
        revision = previous["revision"] if previous else 0
        expected = values.get("expected_revision")
        if expected is not None and (not previous or expected != revision):
            raise self.conflict("stale fixture revision")
        self.rows[key] = {**copy.deepcopy(values), "revision": revision + 1}
        return copy.deepcopy(self.rows[key])

    def get(self, _db, **values):
        return copy.deepcopy(
            self.rows.get((values["namespace"], values["state_key"], values.get("connection_id")))
        )


def receipt_consumers(host, monkeypatch):
    state = host.responses.extension_project_state
    store = ReceiptPersistence(state.ExtensionProjectStateRevisionConflict)
    monkeypatch.setattr(state, "put_state", store.put)
    monkeypatch.setattr(state, "get_state", store.get)
    monkeypatch.setattr(
        state,
        "list_state",
        lambda *a, **kw: pytest.fail("Recovery must use exact keys, never a bounded list"),
    )
    service = SimpleNamespace(
        _get_operation_store=lambda: store,
        _event_command_service_factory=None,
        _command_dispatch_service_factory=None,
    )
    context = SimpleNamespace(
        extension=extension_record(),
        extension_id="flowsteward.instagram",
        scope=host.scope(account_id="account-a", project_id="project-a"),
        audit={
            "account_id": "account-a",
            "project_id": "project-a",
            "connection_id": "connection-account-a",
        },
        bl=store,
    )

    def apply(response):
        invocation = {
            "status": "succeeded" if response["ok"] else "failed",
            "response": response,
            "operation_type": "action",
            "operation_id": "publish_image",
        }
        return host.operations.ExtensionOperationService._process_action_response(
            service, context, invocation
        )

    def hydrate(container_id):
        return host.hydration.load_extension_project_state_for_target(
            store,
            account_id="account-a",
            project_id="project-a",
            extension_id="flowsteward.instagram",
            extension_install_id="fixture-install",
            connection_id="connection-account-a",
            extension=extension_record(),
            input_payload={"container_id": container_id} if container_id else {},
        )

    return store, apply, hydrate


def test_two_actual_initial_pending_outputs_keep_distinct_rows_and_exact_recovery_beyond_list_cap(
    host, graph, monkeypatch
):
    store, apply, hydrate = receipt_consumers(host, monkeypatch)
    assert hydrate(None) == {}
    outputs = []
    for container in (CONTAINER, "70008"):
        graph.reply(200, {"id": container})
        graph.reply(200, {"id": container, "status_code": "IN_PROGRESS"})
        output = invoke(action("publish_image", {"image_url": IMAGE, "caption": "Once"}))
        assert output["ok"] is True
        assert apply(output)["status"] == "succeeded"
        outputs.append(output)
    keys = {row["state_key"] for row in store.rows.values()}
    assert keys == {"container:" + CONTAINER, "container:70008"}
    assert all(row["revision"] == 1 for row in store.rows.values())
    # More unrelated receipts than the largest host list cap cannot hide the old key.
    template = next(iter(store.rows.values()))
    for index in range(1001):
        key = "container:" + str(90000 + index)
        store.rows[("publishing", key, "connection-account-a")] = {**template, "state_key": key}
    hydrated = hydrate(CONTAINER)
    assert [row["state_key"] for row in hydrated["items"]] == ["container:" + CONTAINER]
    request = action("publish_container", {"container_id": CONTAINER})
    request["action"]["target"]["project_state"] = hydrated
    graph.ready()
    graph.published()
    resumed = invoke(request)
    assert resumed["result"]["media_id"] == MEDIA
    assert resumed["project_state_updates"][0]["expected_revision"] == 1
    assert apply(resumed)["status"] == "succeeded"
    before = copy.deepcopy(store.rows)
    stale = apply(resumed)
    assert stale["status"] == "failed"
    assert any(row["code"] == "project_state_revision_conflict" for row in stale["errors"])
    assert store.rows == before
    confirmed, write = confirm(host, monkeypatch, resumed, stale)
    assert confirmed["status"] == "timeout_unknown"
    assert write["external_object_id"] == MEDIA


@pytest.mark.parametrize(
    "field,value",
    [
        ("connection_id", None),
        ("connection_id", "foreign"),
        ("ig_user_id", OTHER_ACCOUNT),
        ("container_id", "70008"),
    ],
)
def test_actual_hydration_foreign_or_project_wide_receipt_is_rejected_before_provider_io(
    host, graph, monkeypatch, field, value
):
    store, _, hydrate = receipt_consumers(host, monkeypatch)
    row = with_receipt(action("publish_container", {"container_id": CONTAINER}))["action"][
        "target"
    ]["project_state"]["items"][0]
    receipt = copy.deepcopy(row["value"])
    receipt[field] = value
    connection = value if field == "connection_id" else "connection-account-a"
    # A foreign connection is not returned by the actual scoped getter; a
    # project-wide fallback can be returned but must still fail runtime ownership.
    store.rows[("publishing", "container:" + CONTAINER, connection)] = {
        "namespace": "publishing",
        "state_key": "container:" + CONTAINER,
        "connection_id": connection,
        "revision": 7,
        "value_jsonb": receipt,
    }
    request = action("publish_container", {"container_id": CONTAINER})
    request["action"]["target"]["project_state"] = hydrate(CONTAINER)
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert graph.requests == []


def ui_health_route(*, saved_account):
    """Choose an actual declared health-panel owner; prefer its selected page.

    The current package has only the ungated Overview route. Keeping that fallback
    reaches the real broken envelope instead of failing on a future query name.
    """
    definitions = yaml.safe_load((ROOT / "ui/queries/queries.yaml").read_text())["queries"]
    queries = {row["query_id"]: row for row in definitions}
    routes = []
    manifest = yaml.safe_load((ROOT / "ui/ui_manifest.yaml").read_text())
    for page_ref in manifest["pages"]:
        page_path = ROOT / "ui" / page_ref["ref"]
        page = yaml.safe_load(page_path.read_text())
        for component_ref in page.get("components", []):
            component = yaml.safe_load((page_path.parent / component_ref["ref"]).read_text())
            if component.get("type") == "health_panel" and component.get("query_id") in queries:
                routes.append((page, component, queries[component["query_id"]]))
    if saved_account:
        selected = [
            route for route in routes if route[0].get("connection") and route[2].get("connection")
        ]
        if selected:
            return selected[0]
    return next(route for route in routes if route[0]["page_id"] == "overview")


def actual_query_readiness(host, monkeypatch, *, saved_account, webhook_secret):
    from core.application.management.requests import ExtensionQueryRequest
    from core.infrastructure.extension_runtime import extension_runtime_invocation

    page, component, query = ui_health_route(saved_account=saved_account)
    extension = {
        **extension_record(),
        "enabled": True,
        "bundle_root": str(ROOT),
        "query_definitions": yaml.safe_load((ROOT / "ui/queries/queries.yaml").read_text())[
            "queries"
        ],
    }
    connection = {
        "connection_id": "connection-account-a",
        "account_id": "account-a",
        "project_id": "project-a",
        "extension_id": "flowsteward.instagram",
        "extension_install_id": "fixture-install",
        "connection_type": "instagram_account",
        "connection_config": {
            "ig_user_id": ACCOUNT,
            "app_id": "50005",
            "graph_api_version": "v27.0",
        },
        "secret_refs": {
            "provider_secrets": {
                "provider_key": "flowsteward.instagram",
                "keys": {
                    "access_token": "fixture/selected/access-token",
                    **({"app_secret": "fixture/selected/app-secret"} if webhook_secret else {}),
                },
            }
        },
        "enabled": True,
    }

    class Store:
        def get_project(self, project_id):
            return {"id": project_id, "account_id": "account-a"}

        def get_project_connection(self, **values):
            if (
                not saved_account
                or values.get("connection_id", "connection-account-a") != "connection-account-a"
            ):
                return None
            return copy.deepcopy(connection)

        def get_provider_secret(self, *, provider_key, secret_key, project_id, include_global):
            assert provider_key == "flowsteward.instagram"
            assert project_id == "project-a" and include_global is False
            # Only this selected connection's fake references resolve; legacy
            # project-global names cannot stand in for its stored credentials.
            return (
                {
                    "fixture/selected/access-token": TOKEN,
                    "fixture/selected/app-secret": APP_SECRET if webhook_secret else None,
                }.get(secret_key)
                if saved_account
                else None
            )

        def log_extension_invocation(self, **values):
            return None

    store = Store()
    captured = []

    def in_process_subprocess(_command, *, input_text, **kwargs):
        payload = json.loads(input_text)
        captured.append(copy.deepcopy(payload))
        response = invoke(payload)
        return (0 if response["ok"] else 2, json.dumps(response), "", False)

    # Retain actual query target resolution, declared resource hydration, host
    # envelope serialization, response parsing and reflection guard. Replace the
    # process/environment boundary: no process, ambient env or provider is used.
    monkeypatch.setattr(
        extension_runtime_invocation, "_run_subprocess_capped", in_process_subprocess
    )
    monkeypatch.setattr(extension_runtime_invocation, "_sandboxed_env", lambda _: {})
    service = host.operations.ExtensionOperationService(
        get_logger=lambda: store,
        get_operation_store=lambda: store,
        resolve_extension_runtime_record=lambda _: (extension, store),
        extension_operation_handler_mode=lambda *a, **kw: "extension",
        extension_operation_failure_status=lambda _: 502,
    )
    body = ExtensionQueryRequest(
        page_id=page["page_id"],
        component_id=component["component_id"],
        context={"project_id": "project-a"},
        target={"connection_id": "connection-account-a"}
        if saved_account and query.get("connection")
        else {},
    )
    result = service.execute_query(
        "flowsteward.instagram",
        query["query_id"],
        body,
        scope=host.scope(
            account_id="account-a",
            project_id="project-a",
            principal_type="operator",
            allowed_project_ids={"project-a"},
        ),
    )
    assert len(captured) == 1
    return result.query["response"], captured[0], page, query


@pytest.mark.parametrize(
    "webhook_secret", [False, True], ids=["outbound-only", "with-webhook-secret"]
)
def test_actual_ui_readiness_query_receives_saved_connection_from_host(
    host, graph, monkeypatch, webhook_secret
):
    response, emitted, page, query = actual_query_readiness(
        host, monkeypatch, saved_account=True, webhook_secret=webhook_secret
    )
    assert response["ok"] is True
    assert response["result"]["configured"] is True, (
        "The actual UI query must report a saved usable account"
    )
    assert response["result"]["webhook_configured"] is webhook_secret
    visible_checks = {row["name"]: row for row in response["result"]["checks"]}
    assert visible_checks["Publishing configuration"]["status"] == "healthy"
    assert visible_checks["Receiving comments"]["status"] == (
        "healthy" if webhook_secret else "warning"
    )
    assert response["result"]["missing_fields"] == []
    assert emitted["target"]["connection"]["connection_id"] == "connection-account-a"
    assert (
        page["connection"]["connection_type_ids"]
        == query["connection"]["connection_type_ids"]
        == ["instagram_account"]
    )
    assert graph.requests == []
    assert TOKEN not in json.dumps(response) and APP_SECRET not in json.dumps(response)


def test_actual_overview_query_stays_ungated_without_saved_account(host, graph, monkeypatch):
    response, emitted, page, query = actual_query_readiness(
        host, monkeypatch, saved_account=False, webhook_secret=False
    )
    assert page["page_id"] == "overview"
    assert "connection" not in page and "connection" not in query
    assert emitted["target"] == {}
    assert response["ok"] is True and response["result"]["configured"] is False
    connection_page = yaml.safe_load((ROOT / "ui/pages/connection.yaml").read_text())
    assert "connection" not in connection_page
    assert graph.requests == []
