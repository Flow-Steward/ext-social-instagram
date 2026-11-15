"""Owned Instagram media/comment reads and replies on one selected account."""

from __future__ import annotations

from typing import Any

from flowsteward_extension_sdk import RuntimeConnection

from handlers.configuration import configured, object_id, pagination, text
from handlers.publishing import acknowledged_id, publish
from handlers.receipts import ContainerReceipts
from handlers.responses import ProviderFailure, WriteBoundary, success
from handlers.transport import GraphClient

WRITES = {"publish_image", "publish_carousel", "publish_container", "reply_to_comment"}
OPERATIONS = WRITES | {
    "get_publish_status",
    "list_posts",
    "get_post",
    "list_comments",
    "list_replies",
    "verify_connection",
}
MEDIA_FIELDS = "id,media_type,media_url,permalink,timestamp,username,owner"


def owned_media(client: GraphClient, media_id: str, account: str) -> dict[str, Any]:
    post = client.request("GET", media_id, {"fields": MEDIA_FIELDS})
    owner = post.get("owner")
    if post.get("id") != media_id or not isinstance(owner, dict) or owner.get("id") != account:
        raise ProviderFailure("foreign_object")
    return post


def comment_media(client: GraphClient, comment_id: str, account: str) -> str:
    comment = client.request("GET", comment_id, {"fields": "id,media"})
    media = comment.get("media")
    if comment.get("id") != comment_id or not isinstance(media, dict):
        raise ProviderFailure("foreign_object")
    try:
        media_id = object_id(media.get("id"))
    except ProviderFailure as error:
        raise ProviderFailure("foreign_object") from error
    owned_media(client, media_id, account)
    return media_id


def listing(
    client: GraphClient, path: str, parameters: dict[str, Any], fields: str
) -> dict[str, Any]:
    response = client.request("GET", path, {**parameters, "fields": fields})
    items = response.get("data")
    if (
        not isinstance(items, list)
        or len(items) > parameters["limit"]
        or not all(isinstance(row, dict) for row in items)
    ):
        raise ProviderFailure("provider_unavailable")
    return success({"items": items, "next_cursor": response.get("_next_cursor", "")})


def execute(
    operation: str,
    values: dict[str, Any],
    connection: RuntimeConnection | None,
    target: dict[str, Any],
    boundary: WriteBoundary,
) -> dict[str, Any]:
    connection = configured(connection)
    account = connection.config["ig_user_id"]
    client = GraphClient(
        connection.config["graph_api_version"],
        connection.secrets["access_token"],
        boundary,
        connection.secrets.get("app_secret", ""),
    )
    if operation in {
        "publish_image",
        "publish_carousel",
        "publish_container",
        "get_publish_status",
    }:
        return publish(operation, values, client, account, ContainerReceipts(target, connection))
    if operation == "verify_connection":
        identity = client.request("GET", account, {"fields": "id,username"})
        if identity.get("id") != account:
            raise ProviderFailure("foreign_object")
        return success({"verified": True, "ig_user_id": account})
    parameters = pagination(values) if operation.startswith("list_") else {}
    if operation == "list_posts":
        return listing(client, account + "/media", parameters, MEDIA_FIELDS)
    if operation in {"get_post", "list_comments"}:
        media_id = object_id(values.get("media_id"))
        post = owned_media(client, media_id, account)
        if operation == "get_post":
            return success({"post": post})
        return listing(
            client, media_id + "/comments", parameters, "id,text,from,timestamp,parent_id"
        )
    comment_id = object_id(values.get("comment_id"))
    message = text(values.get("text"), 2200) if operation == "reply_to_comment" else ""
    comment_media(client, comment_id, account)
    if operation == "list_replies":
        return listing(
            client, comment_id + "/replies", parameters, "id,text,from,timestamp,parent_id"
        )
    response = client.request("POST", comment_id + "/replies", {"message": message})
    reply_id = acknowledged_id(response)
    return success(
        {"comment_id": reply_id}, external_effect_status="succeeded", external_object_id=reply_id
    )
