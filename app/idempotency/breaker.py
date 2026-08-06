import asyncio
import time
from enum import Enum


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AsyncCircuitBreaker:
    def __init__(self, fail_max: int = 5, reset_timeout: float = 60):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.fail_count = 0
        self.last_failure_time = 0
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        if self._state == BreakerState.OPEN:
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self._state = BreakerState.HALF_OPEN
                self.fail_count = 0
        return self._state

    async def call(self, func, *args, **kwargs):
        current_state = self.state

        if current_state == BreakerState.OPEN:
            from redis.exceptions import ConnectionError
            raise ConnectionError("Circuit breaker is open")

        try:
            result = await func(*args, **kwargs)
            if current_state == BreakerState.HALF_OPEN:
                self._state = BreakerState.CLOSED
                self.fail_count = 0
            return result
        except Exception as e:
            self.fail_count += 1
            self.last_failure_time = time.time()

            if self.fail_count >= self.fail_max:
                self._state = BreakerState.OPEN

            raise e
