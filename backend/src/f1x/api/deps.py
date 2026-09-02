"""Shared API dependencies: database engine and response cache.

Cached values are arbitrary JSON and the redis client is untyped, so `Any` is honest
here rather than a gap: narrowing it would mean asserting a shape the cache does not
enforce.
"""

# ruff: noqa: ANN401

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine

from f1x.config import ENGINE_VERSION, get_settings

logger = logging.getLogger(__name__)

# Analysis output only changes when the engine version changes or a session is
# re-ingested, so a long TTL is safe. A day bounds the staleness after a re-ingest
# without making the cache useless.
CACHE_TTL_SECONDS = 86_400


@lru_cache
def get_engine() -> Engine:
    """One pooled engine for the process.

    pool_pre_ping costs a round trip per checkout and prevents the connection-reset
    errors that otherwise surface after the database restarts underneath a long-lived
    API process.
    """
    settings = get_settings()
    return create_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


class ResponseCache:
    """Redis-backed cache for expensive analysis responses.

    Every key includes the engine version, so changing a model invalidates its cached
    results rather than serving values computed under the old definition. That is the
    same discipline the mart tables use, applied at the edge.

    A cache failure is never fatal: Redis being down should make the API slower, not
    broken, so every operation degrades to a miss.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._available = True

    def _connect(self) -> Any:
        if self._client is None and self._available:
            try:
                import redis

                self._client = redis.Redis.from_url(
                    str(get_settings().redis_url),
                    socket_connect_timeout=2,
                    socket_timeout=2,
                    decode_responses=True,
                )
                self._client.ping()
            except Exception as exc:  # noqa: BLE001 - cache is optional by design
                logger.warning("response cache unavailable, serving uncached: %s", exc)
                self._available = False
                self._client = None
        return self._client

    @staticmethod
    def key(route: str, params: dict[str, Any]) -> str:
        """Build a cache key from the route, its parameters and the engine version."""
        payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return f"f1x:{ENGINE_VERSION}:{route}:{digest}"

    def get(self, key: str) -> Any | None:
        client = self._connect()
        if client is None:
            return None
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def set(self, key: str, value: Any, ttl: int = CACHE_TTL_SECONDS) -> None:
        client = self._connect()
        if client is None:
            return
        try:
            client.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception:  # noqa: BLE001
            logger.debug("cache write failed for %s", key)

    def clear(self) -> int:
        """Drop every key for the current engine version. Returns the count removed."""
        client = self._connect()
        if client is None:
            return 0
        try:
            keys = list(client.scan_iter(f"f1x:{ENGINE_VERSION}:*"))
            return int(client.delete(*keys)) if keys else 0
        except Exception:  # noqa: BLE001
            return 0


@lru_cache
def get_cache() -> ResponseCache:
    return ResponseCache()


def cached(route: str) -> Callable[..., Any]:
    """Decorate a handler so its JSON response is cached under the engine version."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache = get_cache()
            key = ResponseCache.key(route, {k: str(v) for k, v in kwargs.items()})
            hit = cache.get(key)
            if hit is not None:
                return hit
            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator
