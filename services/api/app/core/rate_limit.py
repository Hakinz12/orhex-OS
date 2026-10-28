"""Bounded process-local rate limiting for authentication-sensitive routes."""
import asyncio
from collections import OrderedDict, deque
from time import monotonic
from typing import Deque, Dict, Optional, Tuple


class SensitiveEndpointRateLimiter:
    """Sliding-window limiter keyed by the direct network peer address."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        window_seconds: int = 60,
        max_clients: int = 10_000,
    ):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._requests: OrderedDict[str, Deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, client_key: str, now: Optional[float] = None) -> Tuple[bool, int]:
        current = monotonic() if now is None else now
        cutoff = current - self.window_seconds
        async with self._lock:
            requests = self._requests.get(client_key)
            if requests is None:
                requests = deque()
                self._requests[client_key] = requests
            else:
                self._requests.move_to_end(client_key)

            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= self.requests_per_minute:
                retry_after = max(1, int(requests[0] + self.window_seconds - current))
                return False, retry_after

            requests.append(current)
            while len(self._requests) > self.max_clients:
                self._requests.popitem(last=False)
            return True, 0
