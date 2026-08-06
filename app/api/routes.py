import asyncio
import random

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.idempotency.decorator import idempotent

router = APIRouter()


class PaymentRequest(BaseModel):
    user_id: str
    amount: float
    currency: str


class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    amount: float
    currency: str


@router.post("/payments", response_model=PaymentResponse)
@idempotent
async def create_payment(request: Request, payment: PaymentRequest):
    await asyncio.sleep(random.uniform(0.1, 0.5))

    transaction_id = f"txn_{payment.user_id}_{int(asyncio.get_event_loop().time() * 1000)}"

    return PaymentResponse(
        transaction_id=transaction_id,
        status="completed",
        amount=payment.amount,
        currency=payment.currency
    )


@router.post("/orders", response_model=dict)
@idempotent
async def create_order(request: Request, order_data: dict):
    await asyncio.sleep(random.uniform(0.2, 0.8))

    return {
        "order_id": f"ord_{random.randint(10000, 99999)}",
        "status": "confirmed",
        "items": order_data.get("items", []),
        "total": order_data.get("total", 0)
    }
