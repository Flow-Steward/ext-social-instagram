"""Canonical envelopes and conservative remote-write outcome accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProviderFailure(Exception):
    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


@dataclass
class WriteBoundary:
    attempts: int = 0
    rejections: int = 0

    @property
    def no_effect(self) -> bool:
        return self.attempts == self.rejections

    def classify(self, error: ProviderFailure) -> ProviderFailure:
        if not self.no_effect:
            return ProviderFailure("ambiguous_external_effect", uncertain=True)
        return error


def success(result: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "result": result or {},
        "events": [],
        "commands": [],
        "reload": [],
        "errors": [],
        **extra,
    }


def failure(
    error: ProviderFailure,
    *,
    write: bool = False,
    no_effect: bool = False,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message = {
        "invalid_host_envelope": "The host invocation envelope is invalid.",
        "not_configured": "Select a configured Instagram Login connection.",
        "invalid_payload": "The Instagram operation input is invalid.",
        "foreign_object": "The object does not have a verified ownership receipt for this account.",
        "ambiguous_external_effect": "The remote outcome is unknown. Reconcile known IDs before retrying.",
    }.get(error.code, "Instagram request failed (" + error.code + ").")
    response: dict[str, Any] = {
        "ok": False,
        "error_code": error.code,
        "error": message,
        "result": result or {},
        "events": [],
        "commands": [],
        "reload": [],
        "errors": [{"code": error.code, "message": message}],
    }
    if write:
        response["external_effect_status"] = (
            "timeout_unknown" if error.uncertain or not no_effect else "failed"
        )
        if no_effect and not error.uncertain:
            response["definitely_no_external_effect"] = True
    return response
