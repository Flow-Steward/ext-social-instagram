"""Reference-compatible transport boundary reserved for the behavioral RED checkpoint."""


def _http_exchange(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    raise NotImplementedError("No provider transport before observed RED")
