"""Validate the selected public SDK connection and operation inputs."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from flowsteward_extension_sdk import RuntimeConnection

from handlers.responses import ProviderFailure


def object_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
        raise ProviderFailure("invalid_payload")
    return value


def configured(connection: RuntimeConnection | None) -> RuntimeConnection:
    if connection is None or connection.connection_type != "instagram_account":
        raise ProviderFailure("not_configured")
    try:
        object_id(connection.config.get("ig_user_id"))
        object_id(connection.config.get("app_id"))
    except ProviderFailure as error:
        raise ProviderFailure("not_configured") from error
    version = connection.config.get("graph_api_version")
    token = connection.secrets.get("access_token")
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"v[0-9]{1,3}\.[0-9]{1,3}", version)
        or not isinstance(token, str)
        or not token.strip()
        or len(token) > 4096
        or any(ord(c) < 32 for c in token)
    ):
        raise ProviderFailure("not_configured")
    allowlist(connection.config)
    return connection


def allowlist(config: dict[str, Any]) -> set[str]:
    account = object_id(config.get("ig_user_id"))
    if "webhook_account_ids" not in config:
        return {account}
    values = config["webhook_account_ids"]
    if not isinstance(values, list) or len(values) > 100:
        raise ProviderFailure("invalid_payload")
    return {object_id(value) for value in values}


def text(value: Any, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ProviderFailure("invalid_payload")
    return value


def image_url(value: Any) -> str:
    url = text(value, 4096)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in {None, 443}
            or any(ord(c) < 33 or ord(c) == 127 for c in url)
            or "\\" in url
        ):
            raise ValueError("Invalid public URL")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                "." not in host
                or host.lower().endswith((".local", ".localhost", ".internal"))
                or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
            ):
                raise ValueError("Invalid public hostname")
        else:
            if not address.is_global:
                raise ValueError("Non-public address")
    except ValueError as error:
        raise ProviderFailure("invalid_payload") from error
    return url


def pagination(values: dict[str, Any]) -> dict[str, Any]:
    limit = values.get("limit", 25)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ProviderFailure("invalid_payload")
    result: dict[str, Any] = {"limit": limit}
    if "cursor" in values:
        cursor = text(values["cursor"], 2048)
        if any(ord(c) < 32 or ord(c) == 127 for c in cursor):
            raise ProviderFailure("invalid_payload")
        result["after"] = cursor
    return result
