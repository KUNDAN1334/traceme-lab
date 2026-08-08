"""A tiny fixed-window rate limiter.

Nothing in the test-suite depends on this module. It exists so that the
`subtle` break can put a large, loud, completely harmless diff hunk in front of
the model while the real regression hides in a two-line change to auth.py.
"""

from __future__ import annotations

from collections import defaultdict


class RateLimiter:
    """Fixed-window counter keyed by client id."""

    def __init__(self, limit: int = 60, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[int]] = defaultdict(list)

    def _window_start(self, now: int) -> int:
        return now - (now % self.window_seconds)

    def allow(self, client_id: str, now: int) -> bool:
        """Record a hit and report whether the client is still under the limit."""
        start = self._window_start(now)
        hits = [t for t in self._hits[client_id] if t >= start]
        hits.append(now)
        self._hits[client_id] = hits
        return len(hits) <= self.limit

    def remaining(self, client_id: str, now: int) -> int:
        start = self._window_start(now)
        used = len([t for t in self._hits[client_id] if t >= start])
        return max(0, self.limit - used)

    def reset(self, client_id: str) -> None:
        self._hits.pop(client_id, None)
