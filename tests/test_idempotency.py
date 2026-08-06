import json
import hashlib
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from redis.exceptions import ConnectionError as RedisConnectionError
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from app.idempotency.engine import engine


@pytest_asyncio.fixture
async def mock_redis():
    with patch("app.idempotency.engine.redis.Redis") as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance

        mock_instance.ping = AsyncMock()
        mock_instance.get = AsyncMock(return_value=None)
        mock_instance.set = AsyncMock(return_value=True)
        mock_instance.delete = AsyncMock(return_value=1)
        mock_instance.aclose = AsyncMock()

        engine.redis_client = mock_instance

        yield mock_instance

        engine.redis_client = None


@pytest_asyncio.fixture
async def client(mock_redis):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "redis_connected": True,
        "idempotency": "active",
    }


@pytest.mark.asyncio
async def test_health_reports_degraded_without_redis(client, mock_redis):
    mock_redis.ping.side_effect = RedisConnectionError("Redis connection failed")

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "redis_connected": False,
        "idempotency": "degraded",
    }


@pytest.mark.asyncio
async def test_root_endpoint_describes_service(client):
    response = await client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Async Idempotency Engine"
    assert data["redis_connected"] is True
    assert data["endpoints"]["health"] == "/health"


@pytest.mark.asyncio
async def test_metrics_endpoint_served_without_redirect(client):
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert "idempotency_cache_hits_total" in response.text


@pytest.mark.asyncio
async def test_payment_without_idempotency_key(client, mock_redis):
    payload = {
        "user_id": "user123",
        "amount": 100.0,
        "currency": "USD"
    }

    response = await client.post("/api/v1/payments", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["amount"] == 100.0
    assert "transaction_id" in data


@pytest.mark.asyncio
async def test_payment_with_idempotency_key_first_request(client, mock_redis):
    mock_redis.get.return_value = None
    mock_redis.set.return_value = True

    payload = {
        "user_id": "user456",
        "amount": 250.0,
        "currency": "EUR"
    }

    headers = {"X-Idempotency-Key": "test-key-001"}

    response = await client.post("/api/v1/payments", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["amount"] == 250.0
    assert mock_redis.set.called


@pytest.mark.asyncio
async def test_payment_with_idempotency_key_in_progress(client, mock_redis):
    payload = {
        "user_id": "user789",
        "amount": 500.0,
        "currency": "GBP"
    }

    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = hashlib.sha256(f"POST:/api/v1/payments:{payload_str}".encode()).hexdigest()

    mock_redis.set.return_value = None
    mock_redis.get.return_value = json.dumps({
        "status": "in_progress",
        "fingerprint": fingerprint
    })

    headers = {"X-Idempotency-Key": "test-key-002"}

    response = await client.post("/api/v1/payments", json=payload, headers=headers)

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


@pytest.mark.asyncio
async def test_payment_with_idempotency_key_completed(client, mock_redis):
    payload = {
        "user_id": "user999",
        "amount": 750.0,
        "currency": "CAD"
    }

    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = hashlib.sha256(f"POST:/api/v1/payments:{payload_str}".encode()).hexdigest()

    cached_response = {
        "transaction_id": "txn_cached_001",
        "status": "completed",
        "amount": 750.0,
        "currency": "CAD"
    }

    mock_redis.set.return_value = None
    mock_redis.get.return_value = json.dumps({
        "status": "completed",
        "fingerprint": fingerprint,
        "status_code": 200,
        "body": cached_response
    })

    headers = {"X-Idempotency-Key": "test-key-003"}

    response = await client.post("/api/v1/payments", json=payload, headers=headers)

    assert response.status_code == 200
    assert response.json() == cached_response
    assert response.headers.get("Idempotent-Replay") == "true"


@pytest.mark.asyncio
async def test_payment_payload_mismatch(client, mock_redis):
    payload = {
        "user_id": "user555",
        "amount": 300.0,
        "currency": "USD"
    }

    mock_redis.set.return_value = None
    mock_redis.get.return_value = json.dumps({
        "status": "completed",
        "fingerprint": "completely_different_hash_12345",
        "status_code": 200,
        "body": {"transaction_id": "old_txn"}
    })

    headers = {"X-Idempotency-Key": "test-key-004"}

    response = await client.post("/api/v1/payments", json=payload, headers=headers)

    assert response.status_code == 400
    assert "different payload" in response.json()["detail"]


@pytest.mark.asyncio
async def test_order_endpoint_with_idempotency(client, mock_redis):
    mock_redis.get.return_value = None
    mock_redis.set.return_value = True

    payload = {
        "items": ["item1", "item2"],
        "total": 150.0
    }

    headers = {"X-Idempotency-Key": "order-key-001"}

    response = await client.post("/api/v1/orders", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert "order_id" in data


@pytest.mark.asyncio
async def test_degraded_mode_when_redis_unavailable(client, mock_redis):
    mock_redis.set.side_effect = RedisConnectionError("Redis connection failed")
    mock_redis.get.side_effect = RedisConnectionError("Redis connection failed")

    payload = {
        "user_id": "user_degraded",
        "amount": 100.0,
        "currency": "USD"
    }

    headers = {"X-Idempotency-Key": "degraded-key-001"}

    response = await client.post("/api/v1/payments", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
