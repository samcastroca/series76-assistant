"""Login limiter: a sliding window of attempts per (client IP, email), kept in memory.

An attempt is counted when it starts, before the password is checked, so concurrent requests
cannot all pass the check before any failure is recorded. A successful login clears its key, so
in practice the window counts failures.

Enough for one worker process; it resets on restart and sees the proxy's (or Docker gateway's)
IP behind a proxy (ADR 0006). Expired keys are swept once the table grows past `max_keys`, so
rotating emails cannot grow it without bound."""

import math
import time
from collections import deque
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        max_attempts: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.clock = clock
        self.max_keys = max_keys
        self._attempts: dict[tuple[str, str], deque[float]] = {}

    def acquire(self, key: tuple[str, str]) -> int | None:
        """Count one attempt and return None, or, when the window is full, count nothing and
        return the seconds until the next attempt is allowed. Check and count are one step
        (no await in between), so they are atomic on the event loop."""
        if len(self._attempts) >= self.max_keys:
            self._sweep()
        attempts = self._prune(key)
        if len(attempts) >= self.max_attempts:
            return max(1, math.ceil(attempts[0] + self.window_s - self.clock()))
        attempts.append(self.clock())
        self._attempts[key] = attempts
        return None

    def reset(self, key: tuple[str, str]) -> None:
        self._attempts.pop(key, None)

    def _prune(self, key: tuple[str, str]) -> deque[float]:
        """Drop attempts older than the window; keys with none left are removed."""
        attempts = self._attempts.get(key, deque())
        cutoff = self.clock() - self.window_s
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            self._attempts.pop(key, None)
        return attempts

    def _sweep(self) -> None:
        for key in list(self._attempts):
            self._prune(key)
