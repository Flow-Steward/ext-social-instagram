"""Connection-scoped container snapshots; the host performs persistence and CAS."""

from __future__ import annotations

import copy
from typing import Any

from flowsteward_extension_sdk import RuntimeConnection

from handlers.responses import ProviderFailure


class ContainerReceipts:
    def __init__(self, target: dict[str, Any], connection: RuntimeConnection) -> None:
        self.connection = connection
        state = target.get("project_state", {})
        if not isinstance(state, dict):
            raise ProviderFailure("foreign_object")
        rows = state.get("items", [])
        if not isinstance(rows, list):
            raise ProviderFailure("foreign_object")
        self.rows = [copy.deepcopy(row) for row in rows if isinstance(row, dict)]

    def _row(self, container_id: str) -> dict[str, Any] | None:
        matches = [
            row
            for row in self.rows
            if row.get("namespace") == "publishing"
            and row.get("state_key") == "container:" + container_id
        ]
        if len(matches) > 1:
            raise ProviderFailure("foreign_object")
        if not matches:
            return None
        row = matches[0]
        revision = row.get("revision")
        if (
            row.get("connection_id") != self.connection.connection_id
            or type(revision) is not int
            or revision < 1
        ):
            raise ProviderFailure("foreign_object")
        receipt = row.get("value")
        if (
            not isinstance(receipt, dict)
            or receipt.get("container_id") != container_id
            or receipt.get("connection_id") != self.connection.connection_id
            or receipt.get("ig_user_id") != self.connection.config["ig_user_id"]
        ):
            raise ProviderFailure("foreign_object")
        return row

    def require(self, container_id: str) -> dict[str, Any]:
        row = self._row(container_id)
        if row is None:
            raise ProviderFailure("foreign_object")
        return dict(row["value"])

    def record(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        container_id = result["container_id"]
        row = self._row(container_id)
        receipt = {
            "container_id": container_id,
            "connection_id": self.connection.connection_id,
            "ig_user_id": self.connection.config["ig_user_id"],
            **{
                key: copy.deepcopy(result[key])
                for key in ("status", "media_id", "child_container_ids")
                if key in result
            },
        }
        update: dict[str, Any] = {
            "namespace": "publishing",
            "state_key": "container:" + container_id,
            "connection_id": self.connection.connection_id,
            "value_jsonb": receipt,
        }
        if row is not None:
            update["expected_revision"] = row["revision"]
        return [update]
