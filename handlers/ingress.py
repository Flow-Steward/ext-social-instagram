"""Normalize already host-authenticated Instagram comments without network I/O."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from flowsteward_extension_sdk import RuntimeContext

from handlers.configuration import allowlist, object_id
from handlers.responses import ProviderFailure, success

PROVIDER = "flowsteward.instagram"


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize(values: dict[str, Any], context: RuntimeContext) -> dict[str, Any]:
    callback = _object(values.get("callback"))
    request = _object(values.get("request"))
    provider = _object(request.get("provider"))
    anchor = callback.get("connection_id")
    project = callback.get("project_id")
    if (
        callback.get("authenticated") is not True
        or callback.get("extension_id") != PROVIDER
        or callback.get("callback_id") != "comments"
        or not isinstance(anchor, str)
        or not anchor
        or not isinstance(project, str)
        or not project
        or project != context.project_id
        or provider.get("provider_key") != PROVIDER
        or provider.get("project_id") != project
        or provider.get("connection_id") != anchor
        or request.get("method") != "POST"
    ):
        raise ProviderFailure("authentication_failed")
    config = _object(provider.get("provider_config"))
    allowed = allowlist(config)
    # Intake authority and own-author identity are deliberately separate sets.
    own_authors = allowed | {object_id(config.get("ig_user_id"))}
    body = _object(request.get("json"))
    entries = body.get("entry")
    if body.get("object") != "instagram" or not isinstance(entries, list):
        return success(events=[])
    emitted = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        account = entry.get("id")
        if (
            not isinstance(account, str)
            or account not in allowed
            or type(entry.get("time")) is not int
        ):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            change = _object(change)
            value = _object(change.get("value"))
            media = _object(value.get("media"))
            author = _object(value.get("from"))
            if (
                change.get("field") != "comments"
                or value.get("parent_id")
                or value.get("ad_id")
                or media.get("ad_id")
                or media.get("media_product_type", "FEED") not in {"FEED", "CAROUSEL_ALBUM"}
            ):
                continue
            # Conservative inference: the documented self marker identifies a self
            # notification even when from.id uses another Instagram ID namespace.
            if author.get("self_ig_scoped_id"):
                continue
            try:
                comment_id = object_id(value.get("id"))
                media_id = object_id(media.get("id"))
                author_id = object_id(author.get("id"))
            except ProviderFailure:
                continue
            message = value.get("text")
            if author_id in own_authors or not isinstance(message, str):
                continue
            identity = [PROVIDER, project, anchor, account, media_id, comment_id, "created"]
            dedupe = (
                PROVIDER
                + ":comment:"
                + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            )
            payload = {
                "provider": PROVIDER,
                "project_id": project,
                "connection_id": anchor,
                "ig_user_id": account,
                "media_id": media_id,
                "comment_id": comment_id,
                "author_id": author_id,
                "text": message,
                "event_timestamp": entry["time"],
                "action": "created",
                "dedupe_key": dedupe,
            }
            emitted.append(
                {
                    "event_type": "instagram.comment.created",
                    "project_id": project,
                    "source_id": dedupe,
                    "correlation_id": dedupe,
                    "payload": payload,
                }
            )
    return success(events=emitted)
