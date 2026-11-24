"""Generate mixed traffic so the Grafana dashboard has something to show.

Each round sends a burst of concurrent duplicates for one key (one 200, the
rest 409), a few retries after it finishes (cached replays), and sometimes a
changed payload under the same key (400).

    python scripts/load.py --base http://localhost:8000 --seconds 120
"""

import argparse
import asyncio
import random
import time
import uuid
from collections import Counter

import httpx


async def round_trip(client: httpx.AsyncClient, base: str, stats: Counter) -> None:
    key = str(uuid.uuid4())
    body = {"user_id": f"u_{random.randint(1, 500)}", "amount": random.randint(100, 50_000), "currency": "NGN"}
    url = f"{base}/api/v1/payments"
    headers = {"X-Idempotency-Key": key}

    burst = await asyncio.gather(
        *(client.post(url, json=body, headers=headers) for _ in range(random.randint(2, 6)))
    )
    for r in burst:
        stats[r.status_code] += 1

    await asyncio.sleep(0.6)
    for _ in range(random.randint(1, 4)):
        stats[(await client.post(url, json=body, headers=headers)).status_code] += 1

    if random.random() < 0.15:
        changed = {**body, "amount": body["amount"] + 1}
        stats[(await client.post(url, json=changed, headers=headers)).status_code] += 1


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()

    stats: Counter = Counter()
    deadline = time.monotonic() + args.seconds

    async with httpx.AsyncClient(timeout=10) as client:
        async def worker() -> None:
            while time.monotonic() < deadline:
                await round_trip(client, args.base, stats)

        await asyncio.gather(*(worker() for _ in range(args.concurrency)))

    print("status codes:", dict(sorted(stats.items())))


if __name__ == "__main__":
    asyncio.run(main())
