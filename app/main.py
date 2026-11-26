from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.routes import router
from app.idempotency.engine import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.connect()
    yield
    await engine.disconnect()


app = FastAPI(
    title="Async Idempotency Engine",
    description="Production-grade idempotency handling for distributed systems",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(router, prefix="/api/v1")


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    return {
        "service": "Async Idempotency Engine",
        "version": "1.0.0",
        "redis_connected": await engine.ping(),
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "metrics": "/metrics",
            "payments": "POST /api/v1/payments",
            "orders": "POST /api/v1/orders",
        },
        "usage": "Send POST requests with an X-Idempotency-Key header to deduplicate retries.",
    }


@app.get("/livez")
async def livez():
    """Process liveness only. Deliberately ignores Redis: the API keeps serving
    in degraded mode when Redis is down, so a Redis outage must not make
    Kubernetes restart or unroute the pods."""
    return {"status": "alive"}


@app.get("/health")
async def health():
    redis_up = await engine.ping()
    return {
        "status": "healthy",
        "redis_connected": redis_up,
        "idempotency": "active" if redis_up else "degraded",
    }
