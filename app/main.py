from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import make_asgi_app

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

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/health")
async def health():
    return {"status": "healthy"}
