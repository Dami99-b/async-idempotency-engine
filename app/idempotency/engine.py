import asyncio
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram
from redis.exceptions import RedisError

from app.config import settings
from app.idempotency.breaker import AsyncCircuitBreaker, BreakerState

KEY_PREFIX = "idempotency:"

cache_hits = Counter("idempotency_cache_hits_total", "Replayed responses served from cache")
cache_misses = Counter("idempotency_cache_misses_total", "Keys claimed for first-time execution")
lock_conflicts = Counter("idempotency_lock_conflicts_total", "Requests rejected while a sibling holds the lock")
payload_mismatches = Counter("idempotency_payload_mismatch_total", "Retries rejected for a changed payload")
degraded_requests = Counter("idempotency_degraded_total", "Requests passed through while Redis was unreachable")
execution_latency = Histogram("idempotency_execution_seconds", "Wrapped handler latency", ["endpoint", "outcome"])
breaker_state = Gauge("idempotency_breaker_open", "1 when the Redis circuit breaker is open")

TRANSIENT_ERRORS = (RedisError, OSError, asyncio.TimeoutError, ConnectionError)


class Outcome(Enum):
    """Result of claiming an idempotency key."""

    ACQUIRED = "acquired"
    CONFLICT = "conflict"
    REPLAY = "replay"
    MISMATCH = "mismatch"
    DEGRADED = "degraded"


@dataclass(slots=True)
class Claim:
    """Outcome of a claim attempt, carrying the cached record on replay."""

    outcome: Outcome
    status_code: int | None = None
    body: Any = None


class IdempotencyEngine:
    """Redis-backed request deduplication with graceful degradation."""

    def __init__(self):
        self.redis_client: redis.Redis | None = None
        self.breaker = AsyncCircuitBreaker(
            fail_max=settings.circuit_breaker_fail_max,
            reset_timeout=settings.circuit_breaker_timeout,
        )

    async def connect(self):
        self.redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            socket_timeout=settings.redis_timeout,
            socket_connect_timeout=settings.redis_timeout,
            decode_responses=True,
        )
        try:
            await self.redis_client.ping()
        except TRANSIENT_ERRORS:
            pass

    async def disconnect(self):
        if self.redis_client:
            await self.redis_client.aclose()
            self.redis_client = None

    @staticmethod
    def fingerprint(method: str, path: str, payload: Any) -> str:
        """SHA-256 over the route and canonicalised body, so a key cannot be reused across endpoints."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(f"{method}:{path}:{canonical}".encode()).hexdigest()

    async def _call(self, fn, *args, **kwargs):
        if self.redis_client is None:
            raise ConnectionError("redis client unavailable")
        result = await self.breaker.call(fn, *args, **kwargs)
        breaker_state.set(1 if self.breaker.state == BreakerState.OPEN else 0)
        return result

    async def claim(self, key: str, fingerprint: str) -> Claim:
        """Atomically claim a key via SETNX, falling back to a read when it is already held."""
        namespaced = f"{KEY_PREFIX}{key}"
        lock = json.dumps({"status": "in_progress", "fingerprint": fingerprint})

        try:
            acquired = await self._call(
                self.redis_client.set, namespaced, lock, nx=True, ex=settings.idempotency_lock_ttl
            )
        except TRANSIENT_ERRORS:
            breaker_state.set(1 if self.breaker.state == BreakerState.OPEN else 0)
            degraded_requests.inc()
            return Claim(Outcome.DEGRADED)

        if acquired:
            cache_misses.inc()
            return Claim(Outcome.ACQUIRED)

        try:
            raw = await self._call(self.redis_client.get, namespaced)
        except TRANSIENT_ERRORS:
            degraded_requests.inc()
            return Claim(Outcome.DEGRADED)

        if raw is None:
            degraded_requests.inc()
            return Claim(Outcome.DEGRADED)

        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            degraded_requests.inc()
            return Claim(Outcome.DEGRADED)

        if record.get("fingerprint") != fingerprint:
            payload_mismatches.inc()
            return Claim(Outcome.MISMATCH)

        if record.get("status") == "in_progress":
            lock_conflicts.inc()
            return Claim(Outcome.CONFLICT)

        cache_hits.inc()
        return Claim(Outcome.REPLAY, record.get("status_code", 200), record.get("body"))

    async def commit(self, key: str, fingerprint: str, status_code: int, body: Any):
        """Overwrite the lock with the completed response under the long-lived TTL."""
        record = json.dumps(
            {"status": "completed", "fingerprint": fingerprint, "status_code": status_code, "body": body}
        )
        try:
            await self._call(
                self.redis_client.set, f"{KEY_PREFIX}{key}", record, ex=settings.idempotency_ttl
            )
        except TRANSIENT_ERRORS:
            pass

    async def release(self, key: str):
        """Drop the lock so a failed request can be retried rather than replaying its error."""
        try:
            await self._call(self.redis_client.delete, f"{KEY_PREFIX}{key}")
        except TRANSIENT_ERRORS:
            pass


engine = IdempotencyEngine()
