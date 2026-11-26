import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.idempotency.breaker import AsyncCircuitBreaker, BreakerState, CircuitOpenError
from app.main import app


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def ok():
    return "ok"


async def boom():
    raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_opens_after_consecutive_failures():
    breaker = AsyncCircuitBreaker(fail_max=3, reset_timeout=10, clock=FakeClock())
    for _ in range(3):
        with pytest.raises(ConnectionError):
            await breaker.call(boom)
    assert breaker.state is BreakerState.OPEN

    with pytest.raises(CircuitOpenError):
        await breaker.call(ok)


@pytest.mark.asyncio
async def test_success_resets_the_failure_count():
    breaker = AsyncCircuitBreaker(fail_max=3, reset_timeout=10, clock=FakeClock())
    for _ in range(2):
        with pytest.raises(ConnectionError):
            await breaker.call(boom)
    assert await breaker.call(ok) == "ok"
    for _ in range(2):
        with pytest.raises(ConnectionError):
            await breaker.call(boom)
    assert breaker.state is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_success_closes():
    clock = FakeClock()
    breaker = AsyncCircuitBreaker(fail_max=1, reset_timeout=10, clock=clock)
    with pytest.raises(ConnectionError):
        await breaker.call(boom)
    clock.now += 10
    assert breaker.state is BreakerState.HALF_OPEN
    assert await breaker.call(ok) == "ok"
    assert breaker.state is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_failure_reopens_immediately():
    clock = FakeClock()
    breaker = AsyncCircuitBreaker(fail_max=5, reset_timeout=10, clock=clock)
    for _ in range(5):
        with pytest.raises(ConnectionError):
            await breaker.call(boom)
    clock.now += 10
    with pytest.raises(ConnectionError):
        await breaker.call(boom)
    assert breaker.state is BreakerState.OPEN


@pytest.mark.asyncio
async def test_half_open_lets_only_one_probe_through():
    clock = FakeClock()
    breaker = AsyncCircuitBreaker(fail_max=1, reset_timeout=10, clock=clock)
    with pytest.raises(ConnectionError):
        await breaker.call(boom)
    clock.now += 10

    release = asyncio.Event()
    calls = 0

    async def slow_probe():
        nonlocal calls
        calls += 1
        await release.wait()
        return "ok"

    probe = asyncio.create_task(breaker.call(slow_probe))
    await asyncio.sleep(0)
    for _ in range(5):
        with pytest.raises(CircuitOpenError):
            await breaker.call(slow_probe)
    release.set()
    assert await probe == "ok"
    assert calls == 1
    assert breaker.state is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_livez_does_not_touch_redis():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_redis_client_fails_fast():
    from unittest.mock import AsyncMock, patch

    from app.config import settings
    from app.idempotency.engine import engine

    with patch("app.idempotency.engine.redis.Redis") as redis_cls:
        redis_cls.return_value.ping = AsyncMock()
        await engine.connect()
        kwargs = redis_cls.call_args.kwargs
    engine.redis_client = None

    assert kwargs["socket_connect_timeout"] == settings.redis_connect_timeout
    assert kwargs["socket_timeout"] == settings.redis_timeout
    assert kwargs["retry"]._retries == settings.redis_retries
