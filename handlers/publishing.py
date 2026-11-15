"""Image/container orchestration with bounded phases and explicit recovery receipts."""

from __future__ import annotations

from typing import Any

from handlers.configuration import image_url, object_id, text
from handlers.receipts import ContainerReceipts
from handlers.responses import ProviderFailure, failure, success
from handlers.transport import GraphClient

STATUS = {
    "IN_PROGRESS": "pending",
    "FINISHED": "ready",
    "ERROR": "failed",
    "EXPIRED": "expired",
    "PUBLISHED": "published",
}


def status_of(client: GraphClient, container_id: str) -> tuple[str, str]:
    response = client.request("GET", container_id, {"fields": "status_code,status"})
    if response.get("id") not in {None, container_id}:
        raise ProviderFailure("foreign_object")
    code = response.get("status_code")
    if not isinstance(code, str) or code not in STATUS:
        raise ProviderFailure("provider_unavailable")
    return code, STATUS[code]


def acknowledged_id(response: dict[str, Any]) -> str:
    try:
        return object_id(response.get("id"))
    except ProviderFailure as error:
        raise ProviderFailure("ambiguous_external_effect", uncertain=True) from error


def publish(
    operation: str,
    values: dict[str, Any],
    client: GraphClient,
    account: str,
    receipts: ContainerReceipts,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    receipt: dict[str, Any] = {}
    new_parent = operation in {"publish_image", "publish_carousel"}
    # Validate the entire pipeline before its first provider write.
    if new_parent:
        caption = text(values.get("caption"), 2200, empty=True)
        if operation == "publish_image":
            fields: dict[str, Any] = {
                "image_url": image_url(values.get("image_url")),
                "caption": caption,
            }
            if "alt_text" in values:
                fields["alt_text"] = text(values["alt_text"], 1000, empty=True)
            images = []
        else:
            raw = values.get("image_urls")
            if not isinstance(raw, list) or not 2 <= len(raw) <= 20:
                raise ProviderFailure("invalid_payload")
            images = [image_url(value) for value in raw]
            fields = {"media_type": "CAROUSEL", "caption": caption}
    else:
        container_id = object_id(values.get("container_id"))
        receipt = receipts.require(container_id)
        result = {"container_id": container_id, "status": "pending"}
        for key in ("child_container_ids", "media_id"):
            if key in receipt:
                result[key] = receipt[key]

    try:
        if new_parent:
            if images:
                result["child_container_ids"] = []
                for image in images:
                    child = client.request(
                        "POST", account + "/media", {"image_url": image, "is_carousel_item": True}
                    )
                    result["child_container_ids"].append(acknowledged_id(child))
                fields["children"] = result["child_container_ids"]
            parent = client.request("POST", account + "/media", fields)
            result.update(container_id=acknowledged_id(parent), status="pending")
        code, state = status_of(client, result["container_id"])
        if operation == "get_publish_status":
            return success(
                {"container_id": result["container_id"], "status": state, "status_code": code}
            )
        if not new_parent and receipt.get("status") == "published":
            state = "published"
        if state != "ready":
            result["status"] = state
            response = success(result, project_state_updates=receipts.record(result))
            # Pending containers must not be replayed as a completed content publish.
            response["external_effect_status"] = (
                "succeeded" if state == "published" else "timeout_unknown"
            )
            if result.get("media_id"):
                response["external_object_id"] = result["media_id"]
            return response
        published = client.request(
            "POST", account + "/media_publish", {"creation_id": result["container_id"]}
        )
        result.update(status="published", media_id=acknowledged_id(published))
        try:
            post = client.request("GET", result["media_id"], {"fields": "id,permalink"})
            permalink = post.get("permalink")
            if (
                post.get("id") == result["media_id"]
                and isinstance(permalink, str)
                and permalink.startswith("https://www.instagram.com/")
            ):
                result["permalink"] = permalink
        except Exception:
            # Acknowledged media remains published even if optional enrichment fails.
            result.pop("permalink", None)
        return success(
            result,
            external_effect_status="succeeded",
            external_object_id=result["media_id"],
            project_state_updates=receipts.record(result),
        )
    except Exception as error:
        problem = (
            error
            if isinstance(error, ProviderFailure)
            else ProviderFailure("internal_error", uncertain=not client.boundary.no_effect)
        )
        if operation == "get_publish_status":
            raise problem
        if new_parent and result.get("container_id"):
            result["status"] = "pending"
            return success(
                result,
                external_effect_status="timeout_unknown",
                project_state_updates=receipts.record(result),
            )
        result["status"] = "pending" if result.get("container_id") else "failed"
        classified = client.boundary.classify(problem)
        return failure(classified, write=True, no_effect=client.boundary.no_effect, result=result)
