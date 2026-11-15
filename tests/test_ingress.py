"""Signed account-scoped batches normalize without provider I/O or local dedupe."""

from __future__ import annotations

import copy

import pytest
from conftest import (
    ACCOUNT,
    COMMENT,
    MEDIA,
    OTHER_ACCOUNT,
    comment_change,
    delivery,
    ingress,
    invoke,
)


def events(request):
    response = invoke(request)
    assert response["ok"] is True, response
    return response["events"]


def test_new_root_comment_emits_scoped_event_from_authenticated_anchor_without_http(graph):
    emitted = events(ingress())
    assert len(emitted) == 1
    event = emitted[0]
    assert event["event_type"] == "instagram.comment.created"
    assert event["project_id"] == "project-a"
    payload = event["payload"]
    expected = {
        "provider": "flowsteward.instagram",
        "project_id": "project-a",
        "connection_id": "connection-anchor",
        "ig_user_id": ACCOUNT,
        "media_id": MEDIA,
        "comment_id": COMMENT,
        "author_id": "60006",
        "text": "A useful question",
        "event_timestamp": 1794690001,
        "action": "created",
    }
    assert {key: payload[key] for key in expected} == expected
    assert "created_time" not in payload and "created_at" not in payload
    assert event["source_id"] == event["correlation_id"] == payload["dedupe_key"]
    assert "instagram" in event["source_id"]
    assert graph.requests == []


@pytest.mark.parametrize(
    "change",
    [
        comment_change(**{"from": {"id": ACCOUNT}}),
        comment_change(parent_id="60006"),
        comment_change(id=""),
        comment_change(media={}),
        comment_change(**{"from": {}}),
        comment_change(media={"id": MEDIA, "media_product_type": "REELS"}),
        comment_change(media={"id": MEDIA, "media_product_type": "STORY"}),
        comment_change(media={"id": MEDIA, "media_product_type": "FEED", "ad_id": "123"}),
        {"field": "mentions", "value": {"id": COMMENT}},
        {"field": "live_comments", "value": {"id": COMMENT}},
    ],
)
def test_own_comments_replies_incomplete_and_excluded_products_are_ignored(graph, change):
    assert events(ingress(delivery([change]))) == []
    assert graph.requests == []


def test_other_allowed_account_author_is_also_own_account_and_is_ignored():
    assert (
        events(
            ingress(
                delivery([comment_change(**{"from": {"id": OTHER_ACCOUNT}})]),
                webhook_account_ids=[ACCOUNT, OTHER_ACCOUNT],
            )
        )
        == []
    )


def test_foreign_account_entry_is_ignored_even_when_media_claims_same_id():
    assert events(ingress(delivery(account=OTHER_ACCOUNT))) == []


def test_multiple_entries_and_changes_are_scoped_before_normalization():
    body = delivery([comment_change(), comment_change(id="40005")])
    body["entry"].append({"id": OTHER_ACCOUNT, "time": 1794690001, "changes": [comment_change()]})
    emitted = events(ingress(body))
    assert [event["payload"]["comment_id"] for event in emitted] == [COMMENT, "40005"]
    assert len({event["source_id"] for event in emitted}) == 2


def test_explicit_account_allowlist_retains_authenticated_anchor():
    emitted = events(
        ingress(delivery(account=OTHER_ACCOUNT), webhook_account_ids=[ACCOUNT, OTHER_ACCOUNT])
    )
    assert len(emitted) == 1
    assert emitted[0]["payload"]["ig_user_id"] == OTHER_ACCOUNT
    assert emitted[0]["payload"]["connection_id"] == "connection-anchor"
    assert emitted[0]["project_id"] == "project-a"


def test_empty_allowlist_disables_intake_without_implicit_fallback():
    assert events(ingress(webhook_account_ids=[])) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("authenticated", False),
        ("connection_id", ""),
        ("project_id", ""),
        ("callback_id", "foreign-callback"),
        ("extension_id", "foreign.extension"),
    ],
)
def test_unauthenticated_or_unscoped_callback_is_refused(field, value):
    request = ingress()
    request["action"]["input"]["callback"][field] = value
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "authentication_failed"
    assert response["events"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("connection_id", "foreign-anchor"),
        ("project_id", "foreign-project"),
        ("provider_key", "foreign.extension"),
    ],
)
def test_provider_anchor_must_match_authenticated_callback(field, value):
    request = ingress()
    request["action"]["input"]["request"]["provider"][field] = value
    response = invoke(request)
    assert response["ok"] is False
    assert response["events"] == []


def test_body_project_and_connection_cannot_remap_intake():
    body = delivery([comment_change(project_id="victim", connection_id="victim")])
    body.update(project_id="victim", connection_id="victim")
    event = events(ingress(body))[0]
    assert event["project_id"] == "project-a"
    assert event["payload"]["connection_id"] == "connection-anchor"


def test_repeat_delivery_keeps_identity_when_delivery_time_and_text_change():
    original = events(ingress())[0]
    retried = ingress(delivery([comment_change(text="Updated presentation")]))
    retried["action"]["input"]["request"]["json"]["entry"][0]["time"] += 300
    again = events(copy.deepcopy(retried))[0]
    assert original["source_id"] == again["source_id"]
    assert original["correlation_id"] == again["correlation_id"]
    assert original["payload"]["dedupe_key"] == again["payload"]["dedupe_key"]
    # Both are emitted: the existing host ledger owns durable dispatch dedupe.


def test_identity_is_namespaced_by_anchor_account_media_comment_and_created_action():
    original = events(ingress())[0]["source_id"]
    requests = [
        ingress(delivery([comment_change(id="40005")])),
        ingress(delivery([comment_change(media={"id": "30004", "media_product_type": "FEED"})])),
    ]
    request = ingress()
    request["action"]["input"]["callback"]["connection_id"] = "anchor-b"
    request["action"]["input"]["request"]["provider"]["connection_id"] = "anchor-b"
    requests.append(request)
    requests.append(ingress(delivery(account=OTHER_ACCOUNT), webhook_account_ids=[OTHER_ACCOUNT]))
    altered = [events(request)[0]["source_id"] for request in requests]
    assert original not in altered and len(set(altered)) == len(altered)


@pytest.mark.parametrize(
    "body", [{"object": "page", "entry": []}, {"object": "instagram", "entry": []}]
)
def test_empty_or_other_product_batch_emits_no_events(body):
    assert events(ingress(body)) == []
