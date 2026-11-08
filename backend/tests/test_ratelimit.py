"""The login limiter: counts attempts as they start, blocks at the limit, frees the key when the
window passes, resets on success, keeps keys apart and sweeps expired keys. A fake clock drives
time."""

from app.api.ratelimit import LoginRateLimiter

KEY = ("10.0.0.1", "a@example.com")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock, **kwargs) -> LoginRateLimiter:
    return LoginRateLimiter(max_attempts=3, window_s=60, clock=clock, **kwargs)


def test_blocks_after_max_attempts_with_seconds_until_the_oldest_expires() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        assert rl.acquire(KEY) is None
        clock.now += 10
    assert rl.acquire(KEY) == 30  # first attempt at 1000, window 60, now 1030
    assert rl.acquire(KEY) == 30  # a blocked attempt is not counted


def test_window_passing_allows_attempts_again() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.acquire(KEY)
    clock.now += 60
    assert rl.acquire(KEY) is None


def test_reset_clears_the_key() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.acquire(KEY)
    rl.reset(KEY)
    assert rl.acquire(KEY) is None


def test_keys_are_independent() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.acquire(KEY)
    assert rl.acquire(("10.0.0.1", "b@example.com")) is None
    assert rl.acquire(("10.0.0.2", "a@example.com")) is None


def test_expired_keys_are_swept_when_the_table_is_full() -> None:
    clock = Clock()
    rl = limiter(clock, max_keys=5)
    for i in range(5):
        rl.acquire(("10.0.0.1", f"{i}@example.com"))
    clock.now += 60
    rl.acquire(KEY)
    assert list(rl._attempts) == [KEY]
