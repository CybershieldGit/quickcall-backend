"""
Lightweight in-process sliding-window rate limiter for public, cost-incurring endpoints
(web chat, brochure dispatch, simulator). Per-process only; put a gateway limit in front
for multi-instance deployments.
"""
import time
import threading
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request

_BUCKETS: Dict[str, Deque[float]] = defaultdict(deque)
_LOCK = threading.Lock()
_MAX_KEYS = 50_000


def client_ip(request: Request) -> str:
    # X-Forwarded-For is client-controlled; behind a proxy run uvicorn with
    # --proxy-headers --forwarded-allow-ips so request.client is the real caller.
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(key: str, limit: int, window_seconds: float) -> None:
    """Raises HTTP 429 when `key` has been seen `limit` times within `window_seconds`."""
    now = time.monotonic()
    with _LOCK:
        if len(_BUCKETS) > _MAX_KEYS:
            _BUCKETS.clear()
        bucket = _BUCKETS[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")
        bucket.append(now)


def rate_limit_request(request: Request, scope: str, limit: int, window_seconds: float) -> None:
    enforce_rate_limit(f"{scope}:{client_ip(request)}", limit, window_seconds)
