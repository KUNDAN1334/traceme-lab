"""Token-bucket rate limiter.

Replaces the old fixed-window counter, which rejected legitimate bursts that
arrived right after a window boundary. Buckets refill continuously at
`limit / window_seconds` tokens per second and are capped at `burst`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimiter:
    """Continuously refilling token bucket keyed by client id."""

    limit: int = 60
    window_seconds: int = 60
    burst: int | None = None
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._capacity = float(self.burst if self.burst is not None else self.limit)
        self._rate = self.limit / self.window_seconds

    def _bucket(self, client_id: str, now: float) -> _Bucket:
        bucket = self._buckets.get(client_id)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated_at=now)
            self._buckets[client_id] = bucket
        return bucket

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
        bucket.updated_at = now

    def allow(self, client_id: str, now: float) -> bool:
        """Spend one token; report whether the client was under the limit."""
        bucket = self._bucket(client_id, now)
        self._refill(bucket, now)
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def remaining(self, client_id: str, now: float) -> int:
        bucket = self._bucket(client_id, now)
        self._refill(bucket, now)
        return int(bucket.tokens)

    def retry_after(self, client_id: str, now: float) -> float:
        """Seconds until one more token is available. 0.0 when allowed now."""
        bucket = self._bucket(client_id, now)
        self._refill(bucket, now)
        if bucket.tokens >= 1.0:
            return 0.0
        return (1.0 - bucket.tokens) / self._rate

    def reset(self, client_id: str) -> None:
        self._buckets.pop(client_id, None)
