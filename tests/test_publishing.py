"""Provider writes must preserve known outcomes and permit explicit container recovery."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import ACCOUNT, CONTAINER, IMAGE, MEDIA, TOKEN, action, invoke, with_receipt


def test_publish_image_creates_checks_readiness_publishes_and_reads_permalink(graph):
    graph.reply(200, {"id": CONTAINER})
    graph.ready()
    graph.published()
    response = invoke(
        action(
            "publish_image",
            {"image_url": IMAGE, "caption": "Hello + café & cats", "alt_text": "A cat"},
        )
    )
    assert response["ok"] is True, response
    assert response["result"] == {
        "container_id": CONTAINER,
        "status": "published",
        "media_id": MEDIA,
        "permalink": "https://www.instagram.com/p/example/",
    }
    assert response["external_effect_status"] == "succeeded"
    assert response["external_object_id"] == MEDIA
    assert [row["method"] for row in graph.requests] == ["POST", "GET", "POST", "GET"]
    assert graph.requests[0]["url"] == f"https://graph.instagram.com/v27.0/{ACCOUNT}/media"
    assert graph.form_body(0) == {
        "image_url": [IMAGE],
        "caption": ["Hello + café & cats"],
        "alt_text": ["A cat"],
    }
    assert graph.requests[2]["url"].endswith(f"/{ACCOUNT}/media_publish")
    assert graph.form_body(2) == {"creation_id": [CONTAINER]}
    assert parse_qs(urlsplit(graph.requests[1]["url"]).query)["fields"] == ["status_code,status"]
    for request in graph.requests:
        assert request["headers"]["Authorization"] == "Bearer " + TOKEN
        assert TOKEN not in request["url"]
        if request["method"] == "POST":
            assert (
                request["headers"]["Content-Type"].split(";", 1)[0]
                == "application/x-www-form-urlencoded"
            )
            assert not urlsplit(request["url"]).query
            assert IMAGE not in request["url"]
        assert 0 < request["timeout"] <= 30
        assert 0 < request["max_bytes"] <= 2 * 1024 * 1024


def test_caption_at_2200_is_sent_intact_without_truncation(graph):
    graph.reply(200, {"id": CONTAINER})
    graph.ready()
    graph.published()
    caption = "é" * 2200
    assert invoke(action("publish_image", {"image_url": IMAGE, "caption": caption}))["ok"] is True
    assert graph.form_body(0)["caption"] == [caption]


@pytest.mark.parametrize(
    "operation,values",
    [
        ("publish_image", {"image_url": IMAGE, "caption": "a" * 2201}),
        ("publish_image", {"image_url": IMAGE, "caption": "x", "alt_text": "a" * 1001}),
        ("publish_image", {"image_url": "http://images.example.org/a.jpg", "caption": "x"}),
        (
            "publish_image",
            {"image_url": "https://user:pass@images.example.org/a.jpg", "caption": "x"},
        ),
        ("publish_image", {"image_url": "https://127.0.0.1/a.jpg", "caption": "x"}),
        ("publish_image", {"image_url": "https://[::1]/a.jpg", "caption": "x"}),
        ("publish_image", {"image_url": "https://10.1.2.3/a.jpg", "caption": "x"}),
        ("publish_image", {"image_url": "artifact://private-image", "caption": "x"}),
        ("publish_carousel", {"image_urls": [IMAGE], "caption": "x"}),
        ("publish_carousel", {"image_urls": [IMAGE] * 21, "caption": "x"}),
        ("publish_carousel", {"image_urls": [{"video_url": IMAGE}, IMAGE], "caption": "x"}),
        ("publish_carousel", {"image_urls": [IMAGE, IMAGE], "caption": "a" * 2201}),
        ("publish_container", {"container_id": "../media_publish"}),
    ],
)
def test_invalid_publish_input_proves_no_external_effect(graph, operation, values):
    response = invoke(action(operation, values))
    assert response["ok"] is False
    assert response["error_code"] == "invalid_payload"
    assert response["definitely_no_external_effect"] is True
    assert graph.requests == []


def test_carousel_accepts_twenty_images_and_publishes_only_complete_parent(graph):
    for number in range(20):
        graph.reply(200, {"id": str(90000 + number)})
    graph.reply(200, {"id": CONTAINER})
    graph.ready()
    graph.published()
    response = invoke(action("publish_carousel", {"image_urls": [IMAGE] * 20, "caption": "Album"}))
    assert response["ok"] is True, response
    assert response["result"]["media_id"] == MEDIA
    children = [str(90000 + number) for number in range(20)]
    assert response["result"]["child_container_ids"] == children
    for index in range(20):
        assert graph.form_body(index) == {"image_url": [IMAGE], "is_carousel_item": ["true"]}
        assert (
            graph.requests[index]["headers"]["Content-Type"].split(";", 1)[0]
            == "application/x-www-form-urlencoded"
        )
        assert not urlsplit(graph.requests[index]["url"]).query
    parent = graph.form_body(20)
    assert parent["media_type"] == ["CAROUSEL"]
    assert len(parent["children"]) == 1
    assert json.loads(parent["children"][0]) == children
    assert parent["caption"] == ["Album"]
    assert (
        graph.requests[20]["headers"]["Content-Type"].split(";", 1)[0]
        == "application/x-www-form-urlencoded"
    )
    assert not urlsplit(graph.requests[20]["url"]).query
    assert sum(row["url"].endswith("/media_publish") for row in graph.requests) == 1


def test_partial_carousel_child_refusal_returns_receipts_and_never_publishes(graph):
    graph.reply(200, {"id": "90001"})
    graph.reply(400, {"error": {"code": 100, "message": "Invalid image"}})
    response = invoke(action("publish_carousel", {"image_urls": [IMAGE] * 3, "caption": "Album"}))
    assert response["ok"] is False
    assert response["result"]["child_container_ids"] == ["90001"]
    assert response["result"]["status"] == "failed"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 2
    assert all(not row["url"].endswith("/media_publish") for row in graph.requests)


def test_pending_container_is_returned_and_durably_recorded_without_background_polling(graph):
    graph.reply(200, {"id": CONTAINER})
    graph.reply(200, {"id": CONTAINER, "status_code": "IN_PROGRESS"})
    response = invoke(
        with_receipt(action("publish_image", {"image_url": IMAGE, "caption": "Soon"}))
    )
    assert response["ok"] is True, response
    assert response["result"]["container_id"] == CONTAINER
    assert response["result"]["status"] == "pending"
    update = response["project_state_updates"][0]
    assert update["namespace"] == "publishing" and update["state_key"] == "containers"
    assert update["expected_revision"] == 7
    assert update.get("connection_id", "connection-account-a") == "connection-account-a"
    assert update["value_jsonb"]["containers"][CONTAINER]["connection_id"] == "connection-account-a"
    assert update["value_jsonb"]["containers"][CONTAINER]["ig_user_id"] == ACCOUNT
    assert update["value_jsonb"]["containers"]["80008"]["status"] == "pending"
    assert TOKEN not in json.dumps(response)
    assert [row["method"] for row in graph.requests] == ["POST", "GET"]


def test_publish_container_resumes_known_connection_receipt_without_creating_media(graph):
    graph.ready()
    graph.published()
    response = invoke(with_receipt(action("publish_container", {"container_id": CONTAINER})))
    assert response["ok"] is True, response
    assert response["result"]["status"] == "published"
    assert response["result"]["media_id"] == MEDIA
    assert [row["method"] for row in graph.requests] == ["GET", "POST", "GET"]
    assert graph.requests[1]["url"].endswith(f"/{ACCOUNT}/media_publish")
    assert graph.form_body(1) == {"creation_id": [CONTAINER]}
    update = response["project_state_updates"][0]
    assert update["expected_revision"] == 7
    assert update.get("connection_id", "connection-account-a") == "connection-account-a"
    assert (
        graph.requests[1]["headers"]["Content-Type"].split(";", 1)[0]
        == "application/x-www-form-urlencoded"
    )
    assert not urlsplit(graph.requests[1]["url"]).query


@pytest.mark.parametrize(
    "status, expected",
    [
        ("IN_PROGRESS", "pending"),
        ("ERROR", "failed"),
        ("EXPIRED", "expired"),
        ("PUBLISHED", "published"),
    ],
)
def test_resume_status_prevents_unready_failed_expired_or_already_published_resend(
    graph, status, expected
):
    graph.reply(200, {"id": CONTAINER, "status_code": status})
    response = invoke(with_receipt(action("publish_container", {"container_id": CONTAINER})))
    assert response["result"]["status"] == expected
    assert response["result"]["container_id"] == CONTAINER
    assert [row["method"] for row in graph.requests] == ["GET"]
    if status == "PUBLISHED":
        assert response["ok"] is True
        assert response["external_effect_status"] == "succeeded"


@pytest.mark.parametrize(
    "account,connection", [("20002", "connection-account-a"), (ACCOUNT, "foreign-connection")]
)
def test_foreign_container_receipt_cannot_be_resumed(graph, account, connection):
    response = invoke(
        with_receipt(
            action("publish_container", {"container_id": CONTAINER}),
            account=account,
            connection=connection,
        )
    )
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert response["definitely_no_external_effect"] is True
    assert graph.requests == []


def test_unknown_container_is_not_recreated_or_published(graph):
    response = invoke(action("publish_container", {"container_id": CONTAINER}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert graph.requests == []


@pytest.mark.parametrize("status", ["IN_PROGRESS", "FINISHED", "ERROR", "EXPIRED", "PUBLISHED"])
def test_get_publish_status_is_read_only_even_when_finished(graph, status):
    graph.reply(200, {"id": CONTAINER, "status_code": status})
    response = invoke(with_receipt(action("get_publish_status", {"container_id": CONTAINER})))
    assert response["ok"] is True, response
    assert response["result"]["status_code"] == status
    assert [row["method"] for row in graph.requests] == ["GET"]
    assert not response.get("project_state_updates")


def test_timeout_creating_container_is_ambiguous_and_never_retried(graph):
    graph.fail(TimeoutError("May have created " + TOKEN))
    response = invoke(action("publish_image", {"image_url": IMAGE, "caption": "Once"}))
    assert response["ok"] is False
    assert response["error_code"] == "ambiguous_external_effect"
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 1
    assert TOKEN not in json.dumps(response)


def test_readiness_timeout_retains_created_container_for_explicit_resume(graph):
    graph.reply(200, {"id": CONTAINER})
    graph.fail(TimeoutError("Read timed out"))
    response = invoke(action("publish_image", {"image_url": IMAGE, "caption": "Once"}))
    assert response["ok"] is True, response
    assert response["result"]["container_id"] == CONTAINER
    assert response["result"]["status"] == "pending"
    update = response["project_state_updates"][0]
    assert update["namespace"] == "publishing" and update["state_key"] == "containers"
    receipt = update["value_jsonb"]["containers"][CONTAINER]
    assert receipt["container_id"] == CONTAINER
    assert receipt["ig_user_id"] == ACCOUNT
    assert receipt["connection_id"] == "connection-account-a"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 2


@pytest.mark.parametrize("operation", ["publish_image", "publish_carousel"])
@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "http200_array_code",
        "http503_map_code",
        "post_send_value_error",
        "post_send_type_error",
        "post_send_key_error",
    ],
)
def test_new_parent_finalization_ambiguity_returns_handled_pending_receipt(
    graph, operation, failure
):
    if operation == "publish_carousel":
        graph.reply(200, {"id": "90001"})
        graph.reply(200, {"id": "90002"})
        values = {"image_urls": [IMAGE, IMAGE], "caption": "Once"}
    else:
        values = {"image_url": IMAGE, "caption": "Once"}
    graph.reply(200, {"id": CONTAINER})
    graph.ready()
    if failure == "timeout":
        graph.fail(TimeoutError("Publish may have succeeded " + TOKEN))
    elif failure == "http200_array_code":
        graph.reply(200, {"error": {"code": [190], "message": TOKEN}})
    elif failure == "http503_map_code":
        graph.reply(503, {"error": {"code": {"unexpected": 2}, "message": TOKEN}})
    else:
        exception = {
            "post_send_value_error": ValueError,
            "post_send_type_error": TypeError,
            "post_send_key_error": KeyError,
        }[failure]
        graph.fail(exception("Processing failed after send " + TOKEN))
    request = with_receipt(action(operation, values))
    del request["action"]["target"]["project_state"]["items"][0]["value"]["containers"][CONTAINER]
    response = invoke(request)
    # Failed invocation envelopes bypass host receipt processing. Handled pending
    # persists recovery state; explicit unknown metadata prevents a success claim.
    assert response["ok"] is True, response
    assert response["result"]["status"] == "pending"
    assert response["result"]["container_id"] == CONTAINER
    assert not response["result"].get("media_id")
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    update = response["project_state_updates"][0]
    assert update["namespace"] == "publishing" and update["state_key"] == "containers"
    assert update["expected_revision"] == 7
    assert update.get("connection_id", "connection-account-a") == "connection-account-a"
    receipt = update["value_jsonb"]["containers"][CONTAINER]
    assert receipt["container_id"] == CONTAINER
    assert receipt["ig_user_id"] == ACCOUNT
    assert receipt["connection_id"] == "connection-account-a"
    assert update["value_jsonb"]["containers"]["80008"]["status"] == "pending"
    assert graph.requests[-1]["url"].endswith(f"/{ACCOUNT}/media_publish")
    assert sum(row["url"].endswith("/media_publish") for row in graph.requests) == 1
    assert len(graph.requests) == (5 if operation == "publish_carousel" else 3)
    assert TOKEN not in json.dumps(response)


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("code", [[190], {"unexpected": 2}])
def test_malformed_error_after_container_create_send_is_unknown_not_no_effect(graph, status, code):
    graph.reply(status, {"error": {"code": code, "message": "Untrusted " + TOKEN}})
    response = invoke(action("publish_image", {"image_url": IMAGE, "caption": "Once"}))
    assert response["ok"] is False
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 1
    assert TOKEN not in json.dumps(response)


@pytest.mark.parametrize("exception", [ValueError, TypeError, KeyError])
def test_processing_exception_after_container_create_send_is_unknown_not_no_effect(
    graph, exception
):
    graph.fail(exception("Post-send processing " + TOKEN))
    response = invoke(action("publish_image", {"image_url": IMAGE, "caption": "Once"}))
    assert response["ok"] is False
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 1
    assert TOKEN not in json.dumps(response)


def test_timeout_finalizing_container_is_unknown_with_id_retained_for_read_first_recovery(graph):
    graph.ready()
    graph.fail(TimeoutError("Publish may have succeeded"))
    response = invoke(with_receipt(action("publish_container", {"container_id": CONTAINER})))
    assert response["ok"] is False
    assert response["external_effect_status"] == "timeout_unknown"
    assert response["result"]["container_id"] == CONTAINER
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 2


def test_media_id_acknowledgement_stays_published_when_permalink_read_times_out(graph):
    graph.ready()
    graph.reply(200, {"id": MEDIA})
    graph.fail(TimeoutError("Permalink unavailable"))
    response = invoke(with_receipt(action("publish_container", {"container_id": CONTAINER})))
    assert response["ok"] is True, response
    assert response["result"]["status"] == "published"
    assert response["result"]["media_id"] == MEDIA
    assert not response["result"].get("permalink")
    assert response["external_effect_status"] == "succeeded"
    assert response["external_object_id"] == MEDIA
    assert sum(row["method"] == "POST" for row in graph.requests) == 1


def test_first_provider_refusal_is_definitive_no_effect(graph):
    graph.reply(403, {"error": {"code": 200, "message": "Forbidden"}})
    response = invoke(action("publish_image", {"image_url": IMAGE, "caption": "No"}))
    assert response["ok"] is False
    assert response["external_effect_status"] == "failed"
    assert response["definitely_no_external_effect"] is True
    assert len(graph.requests) == 1
