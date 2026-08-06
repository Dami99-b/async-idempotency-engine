import json
import time
from functools import wraps
from typing import Any, Callable

from fastapi import HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.idempotency.engine import Outcome, engine, execution_latency

REPLAY_HEADER = "Idempotent-Replay"


def _serialize(result: Any) -> tuple[int, Any]:
    """Reduce a handler's return value to a cacheable (status_code, json_body) pair."""
    if isinstance(result, JSONResponse):
        return result.status_code, json.loads(bytes(result.body).decode())
    if isinstance(result, Response):
        raise TypeError("@idempotent cannot cache raw Response objects; return a model, dict, or JSONResponse")
    return 200, jsonable_encoder(result)


def idempotent(func: Callable):
    """Deduplicate handlers keyed by the X-Idempotency-Key header.

    New keys execute and cache; in-flight keys get 409; replays return the cached
    body; a changed payload under a known key gets 400. If Redis is unreachable the
    circuit breaker trips and requests pass through unprotected.
    """

    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        key = request.headers.get("X-Idempotency-Key")
        if not key:
            return await func(request, *args, **kwargs)

        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None

        fingerprint = engine.fingerprint(request.method, request.url.path, payload)
        claim = await engine.claim(key, fingerprint)

        if claim.outcome is Outcome.CONFLICT:
            raise HTTPException(
                status_code=409,
                detail="A request with this idempotency key is already in progress",
                headers={"Retry-After": "1"},
            )

        if claim.outcome is Outcome.MISMATCH:
            raise HTTPException(
                status_code=400,
                detail="Idempotency key was already used with a different payload",
            )

        if claim.outcome is Outcome.REPLAY:
            return JSONResponse(
                content=claim.body,
                status_code=claim.status_code,
                headers={REPLAY_HEADER: "true"},
            )

        endpoint = request.url.path
        started = time.perf_counter()
        try:
            result = await func(request, *args, **kwargs)
        except Exception:
            execution_latency.labels(endpoint, "error").observe(time.perf_counter() - started)
            if claim.outcome is Outcome.ACQUIRED:
                await engine.release(key)
            raise

        execution_latency.labels(endpoint, claim.outcome.value).observe(time.perf_counter() - started)

        if claim.outcome is Outcome.ACQUIRED:
            try:
                status_code, body = _serialize(result)
            except TypeError:
                await engine.release(key)
                raise
            await engine.commit(key, fingerprint, status_code, body)

        return result

    return wrapper
