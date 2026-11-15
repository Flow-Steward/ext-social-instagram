"""Interrupt blocking SDK acquisition/reads within one subprocess exchange budget."""

from __future__ import annotations

import math
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

from handlers.responses import ProviderFailure


class _DeadlineExpired(BaseException):
    """Escape SDK per-address `except Exception` retry boundaries, then normalize."""


@contextmanager
def blocking_deadline(seconds: float) -> Iterator[None]:
    """Use the main-thread POSIX timer, retaining any caller handler/timer.

    Provider runtime is a single main-thread subprocess. Unsupported contexts
    fail before I/O rather than spawning a reader that cannot be cancelled.
    An earlier callable caller alarm is delivered, including its interval; an
    earlier default/unknown handler is left untouched and the exchange refused.
    """
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise ProviderFailure("provider_unavailable")
    started = time.monotonic()
    deadline = started + seconds
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    previous_at = started + previous_delay if previous_delay > 0 else None
    if previous_handler is None or (
        previous_at is not None and previous_at <= deadline and previous_handler == signal.SIG_DFL
    ):
        raise ProviderFailure("provider_unavailable")

    def arm() -> None:
        now = time.monotonic()
        next_at = min(deadline, previous_at) if previous_at is not None else deadline
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, next_at - now))

    def expired(number: int, frame: FrameType | None) -> None:
        nonlocal previous_at
        now = time.monotonic()
        if now >= deadline and (previous_at is None or deadline <= previous_at):
            raise _DeadlineExpired
        if previous_at is not None and now >= previous_at:
            previous_at = (
                previous_at
                + (math.floor((now - previous_at) / previous_interval) + 1) * previous_interval
                if previous_interval > 0
                else None
            )
            arm()
            if callable(previous_handler):
                previous_handler(number, frame)
        if time.monotonic() >= deadline:
            raise _DeadlineExpired
        arm()

    signal.signal(signal.SIGALRM, expired)
    try:
        arm()
        yield
    except _DeadlineExpired:
        raise TimeoutError("Instagram exchange deadline exhausted") from None
    finally:
        # Cancel our timer before restoring a caller handler. Preserve its elapsed
        # countdown (or schedule an overdue caller alarm promptly), not a fresh delay.
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        remaining = (
            max(0.000001, previous_at - time.monotonic()) if previous_at is not None else 0.0
        )
        signal.setitimer(signal.ITIMER_REAL, remaining, previous_interval)
