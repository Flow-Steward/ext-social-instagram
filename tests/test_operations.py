"""Selected-account reads, ownership proofs, replies and bounded Graph failures."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import ACCOUNT, COMMENT, IMAGE, MEDIA, OTHER_ACCOUNT, TOKEN, action, envelope, invoke


def owned_media(graph, owner=ACCOUNT):
    graph.reply(
        200, {"id": MEDIA, "owner": {"id": owner}, "media_type": "IMAGE", "caption": "Hello"}
    )


def comment_parent(graph):
    graph.reply(200, {"id": COMMENT, "media": {"id": MEDIA}, "text": "Question"})


def test_list_posts_uses_selected_account_and_returns_opaque_cursor(graph):
    request = action("list_posts", {"limit": 7, "cursor": "input+/="}, ig_user_id=OTHER_ACCOUNT)
    request["action"]["target"]["connection"]["secrets"]["access_token"] = "fixture-token-b"
    graph.reply(
        200,
        {
            "data": [{"id": MEDIA}],
            "paging": {
                "cursors": {"after": "opaque+/="},
                "next": "https://evil.example/steal?access_token=" + TOKEN,
            },
        },
    )
    response = invoke(request)
    assert response["ok"] is True, response
    assert response["result"]["items"] == [{"id": MEDIA}]
    assert response["result"]["next_cursor"] == "opaque+/="
    parsed = urlsplit(graph.requests[0]["url"])
    assert parsed.hostname == "graph.instagram.com"
    assert parsed.path == f"/v27.0/{OTHER_ACCOUNT}/media"
    assert parse_qs(parsed.query)["limit"] == ["7"]
    assert parse_qs(parsed.query)["after"] == ["input+/="]
    assert graph.requests[0]["headers"]["Authorization"] == "Bearer fixture-token-b"
    assert "evil.example" not in json.dumps(response)
    assert len(graph.requests) == 1


def test_get_post_returns_owned_media_only(graph):
    owned_media(graph)
    response = invoke(action("get_post", {"media_id": MEDIA}))
    assert response["ok"] is True, response
    assert response["result"]["post"]["id"] == MEDIA
    assert [row["method"] for row in graph.requests] == ["GET"]


@pytest.mark.parametrize("operation", ["get_post", "list_comments"])
def test_foreign_media_owner_blocks_asset_access(graph, operation):
    owned_media(graph, OTHER_ACCOUNT)
    response = invoke(action(operation, {"media_id": MEDIA}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert len(graph.requests) == 1


@pytest.mark.parametrize(
    "operation", ["get_post", "list_comments", "list_replies", "reply_to_comment"]
)
def test_missing_media_owner_blocks_asset_access_and_replies(graph, operation):
    comment_operation = operation in {"list_replies", "reply_to_comment"}
    if comment_operation:
        comment_parent(graph)
    graph.reply(200, {"id": MEDIA, "username": "configured-account-name"})
    values = (
        {"comment_id": COMMENT, "text": "No ownership proof"}
        if comment_operation
        else {"media_id": MEDIA}
    )
    response = invoke(action(operation, values))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert [row["method"] for row in graph.requests] == ["GET"] * (2 if comment_operation else 1)
    if operation == "reply_to_comment":
        assert response["definitely_no_external_effect"] is True


@pytest.mark.parametrize("operation", ["list_replies", "reply_to_comment"])
def test_foreign_comment_media_blocks_read_or_reply(graph, operation):
    comment_parent(graph)
    owned_media(graph, OTHER_ACCOUNT)
    response = invoke(action(operation, {"comment_id": COMMENT, "text": "Thanks"}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert [row["method"] for row in graph.requests] == ["GET", "GET"]
    if operation == "reply_to_comment":
        assert response["definitely_no_external_effect"] is True


def test_comment_without_media_relationship_cannot_be_replied_to(graph):
    graph.reply(200, {"id": COMMENT})
    response = invoke(action("reply_to_comment", {"comment_id": COMMENT, "text": "No proof"}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert response["definitely_no_external_effect"] is True
    assert len(graph.requests) == 1


def test_reply_checks_comment_and_media_owner_before_posting(graph):
    comment_parent(graph)
    owned_media(graph)
    graph.reply(200, {"id": "90009"})
    response = invoke(
        action("reply_to_comment", {"comment_id": COMMENT, "text": "Thanks + café & friends"})
    )
    assert response["ok"] is True, response
    assert response["result"]["comment_id"] == "90009"
    assert response["external_effect_status"] == "succeeded"
    assert response["external_object_id"] == "90009"
    assert [row["method"] for row in graph.requests] == ["GET", "GET", "POST"]
    assert graph.requests[-1]["url"].endswith(f"/{COMMENT}/replies")
    assert graph.form_body() == {"message": ["Thanks + café & friends"]}
    assert (
        graph.requests[-1]["headers"]["Content-Type"].split(";", 1)[0]
        == "application/x-www-form-urlencoded"
    )
    assert not urlsplit(graph.requests[-1]["url"]).query


@pytest.mark.parametrize(
    "operation, values, edge, preflight",
    [
        ("list_comments", {"media_id": MEDIA}, f"/{MEDIA}/comments", 1),
        ("list_replies", {"comment_id": COMMENT}, f"/{COMMENT}/replies", 2),
    ],
)
def test_comment_lists_are_bounded_and_do_not_follow_provider_paging_url(
    graph, operation, values, edge, preflight
):
    if preflight == 2:
        comment_parent(graph)
    owned_media(graph)
    graph.reply(
        200,
        {
            "data": [{"id": COMMENT, "text": "Hi"}],
            "paging": {"cursors": {"after": "next+/="}, "next": "https://evil.example/"},
        },
    )
    response = invoke(action(operation, {**values, "limit": 4, "cursor": "before+/="}))
    assert response["ok"] is True, response
    assert response["result"]["items"] == [{"id": COMMENT, "text": "Hi"}]
    assert response["result"]["next_cursor"] == "next+/="
    parsed = urlsplit(graph.requests[-1]["url"])
    assert parsed.hostname == "graph.instagram.com" and parsed.path.endswith(edge)
    assert parse_qs(parsed.query)["limit"] == ["4"]
    assert parse_qs(parsed.query)["after"] == ["before+/="]
    assert len(graph.requests) == preflight + 1


@pytest.mark.parametrize("missing", ["ig_user_id", "app_id", "graph_api_version", "access_token"])
def test_missing_configuration_prevents_provider_writes(graph, missing):
    request = action("publish_image", {"image_url": IMAGE, "caption": "Hello"})
    request["action"]["target"]["connection"][
        "secrets" if missing == "access_token" else "config"
    ].pop(missing)
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "not_configured"
    assert response["definitely_no_external_effect"] is True
    assert graph.requests == []


def test_missing_selected_connection_does_not_discover_another_account(graph):
    request = action("list_posts")
    request["action"]["target"] = {}
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "not_configured"
    assert graph.requests == []


@pytest.mark.parametrize(
    "operation,values",
    [
        ("get_post", {"media_id": "../me"}),
        ("reply_to_comment", {"comment_id": "4?token=bad", "text": "No"}),
        ("reply_to_comment", {"comment_id": COMMENT, "text": ""}),
        ("list_posts", {"limit": True}),
        ("list_posts", {"limit": 0}),
        ("list_posts", {"limit": 101}),
    ],
)
def test_invalid_operation_inputs_are_refused_without_traffic(graph, operation, values):
    response = invoke(action(operation, values))
    assert response["ok"] is False
    assert response["error_code"] == "invalid_payload"
    assert graph.requests == []


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, 190, "authentication_failed"),
        (403, 200, "permission_denied"),
        (429, 4, "rate_limited"),
        (503, 2, "provider_unavailable"),
    ],
)
def test_provider_error_classification_never_echoes_tokens(graph, capsys, status, code, expected):
    graph.reply(status, {"error": {"code": code, "message": "unsafe " + TOKEN}})
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert response["error_code"] == expected
    assert response["errors"][0] == {"code": expected, "message": response["error"]}
    captured = capsys.readouterr()
    assert TOKEN not in json.dumps(response) + captured.out + captured.err


def test_read_timeout_is_read_failure_without_external_effect_metadata(graph):
    graph.fail(TimeoutError("Read failed " + TOKEN))
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert response["error_code"] == "timeout"
    assert response.get("external_effect_status") != "timeout_unknown"
    assert TOKEN not in json.dumps(response)


def test_provider_redirect_is_not_followed(graph):
    graph.reply(302, b"", {"Location": "https://evil.example/"})
    assert invoke(action("list_posts"))["ok"] is False
    assert len(graph.requests) == 1


def test_oversized_provider_response_is_bounded(graph):
    graph.reply(200, b"x" * (2 * 1024 * 1024 + 1))
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert response["error_code"] == "response_too_large"


def test_verify_connection_reads_and_compares_configured_account(graph):
    graph.reply(200, {"id": ACCOUNT, "username": "account"})
    response = invoke(action("verify_connection"))
    assert response["ok"] is True, response
    assert response["result"]["verified"] is True
    assert response["result"]["ig_user_id"] == ACCOUNT
    assert [row["method"] for row in graph.requests] == ["GET"]


def test_verify_connection_rejects_wrong_account(graph):
    graph.reply(200, {"id": OTHER_ACCOUNT})
    response = invoke(action("verify_connection"))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"


def test_readiness_reports_missing_configuration_without_provider_calls_or_secrets(graph):
    response = invoke(envelope({"mode": "query", "query": {"query_id": "provider_health"}}))
    assert response["ok"] is True, response
    assert response["result"]["configured"] is False
    assert "ig_user_id" in response["result"]["missing_fields"]
    assert graph.requests == []
    assert TOKEN not in json.dumps(response)
