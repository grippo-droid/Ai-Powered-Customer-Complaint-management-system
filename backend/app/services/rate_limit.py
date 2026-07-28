"""
A small per-IP rate limiter for the public demo deployment.

Only one endpoint spends money: POST /complaints/session/{id}/message, which
runs the LangGraph pipeline and therefore calls Groq. The Groq free tier is a
single daily token budget shared by every visitor, so one person repeatedly
uploading documents can exhaust the day for everybody. This caps that.

Deliberately in-memory, with no Redis and no extra dependency:

  * The demo runs as a single process on one instance, so a shared store
    would buy nothing. If this were ever scaled to several instances each
    would keep its own counter, and the effective limit would multiply by
    the instance count - that is the known trade-off, not an oversight.
  * Counters reset when the process restarts. On a free tier that sleeps
    after idling, a restart means traffic had already stopped, so the reset
    costs nothing in practice.

Locally the limit is disabled (RATE_LIMIT_PER_HOUR=0), so development and
the demo recording are never throttled.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import Request

WINDOW_SECONDS = 3600  # the limit is expressed per hour

# One deque of request timestamps per client key, newest at the right.
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()

# Sweeping every call would be O(clients) on every request. Instead we sweep
# at most once a minute, which keeps the dict from growing without bound
# while leaving the hot path proportional to one client's own history.
_last_sweep = 0.0
_SWEEP_INTERVAL = 60.0


class RateLimitExceeded(Exception):
    """Raised when a client is over its hourly allowance."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        minutes = max(1, round(self.retry_after_seconds / 60))
        super().__init__(
            f"You've reached the demo limit for this hour. This is a public demo "
            f"running on a shared free-tier AI quota, so each visitor gets a "
            f"capped number of AI requests. Please try again in about "
            f"{minutes} minute{'s' if minutes != 1 else ''}."
        )


def client_key(request: Request) -> str:
    """
    Identify the caller.

    Render (like most PaaS hosts) terminates TLS at a proxy, so request.client
    is the proxy, not the visitor. The real address is the first entry in
    X-Forwarded-For - the left-most value is the original client, and every
    hop appends itself to the right.

    A determined user can spoof that header, which is exactly why this is a
    courtesy limit for a demo and not a security control. The genuine ceiling
    on spend is the Groq account quota itself.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Drop clients whose whole window has expired. Caller must hold the lock."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL:
        return
    _last_sweep = now

    cutoff = now - WINDOW_SECONDS
    stale = [key for key, hits in _hits.items() if not hits or hits[-1] <= cutoff]
    for key in stale:
        del _hits[key]


def check_rate_limit(key: str, limit_per_hour: int) -> None:
    """
    Record one request for `key` and raise if it is over the limit.

    A sliding window, not a fixed one: we keep the timestamps of the last
    hour's requests and expire them individually. A fixed hourly bucket would
    let someone spend a full allowance at 10:59 and another at 11:00; here the
    allowance frees up gradually, which is both fairer and harder to game.

    `limit_per_hour <= 0` disables the check entirely.
    """
    if limit_per_hour <= 0:
        return

    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS

    with _lock:
        _sweep(now)
        hits = _hits[key]

        # Expire from the left - the deque is ordered, so we can stop at the
        # first timestamp still inside the window.
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= limit_per_hour:
            # The oldest request is the one whose expiry frees a slot.
            raise RateLimitExceeded(int(hits[0] + WINDOW_SECONDS - now) + 1)

        hits.append(now)


def reset() -> None:
    """Clear all counters. Used by tests - never called by the application."""
    with _lock:
        _hits.clear()
