import time
from enum import Enum


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(ConnectionError):
    """Raised without touching Redis while the breaker is open."""


class AsyncCircuitBreaker:
    """Consecutive-failure circuit breaker for async calls.

    CLOSED: calls go through; ``fail_max`` consecutive failures open the breaker.
    OPEN: calls fail immediately until ``reset_timeout`` seconds have passed.
    HALF_OPEN: exactly one probe call is let through. Success closes the
    breaker, failure reopens it straight away. Other callers fail fast while
    the probe is in flight, so an outage never queues up a crowd of slow calls.
    """

    def __init__(self, fail_max: int = 5, reset_timeout: float = 60, clock=time.monotonic):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.fail_count = 0
        self._clock = clock
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self.reset_timeout:
            self._state = BreakerState.HALF_OPEN
        return self._state

    async def call(self, func, *args, **kwargs):
        state = self.state

        if state is BreakerState.OPEN:
            raise CircuitOpenError("circuit breaker is open")

        if state is BreakerState.HALF_OPEN:
            if self._probe_in_flight:
                raise CircuitOpenError("circuit breaker is half-open and a probe is in flight")
            self._probe_in_flight = True

        try:
            result = await func(*args, **kwargs)
        except Exception:
            self._record_failure(state)
            raise
        else:
            self._record_success()
            return result
        finally:
            if state is BreakerState.HALF_OPEN:
                self._probe_in_flight = False

    def _record_failure(self, state: BreakerState) -> None:
        self.fail_count += 1
        if state is BreakerState.HALF_OPEN or self.fail_count >= self.fail_max:
            self._state = BreakerState.OPEN
            self._opened_at = self._clock()

    def _record_success(self) -> None:
        self.fail_count = 0
        self._state = BreakerState.CLOSED
