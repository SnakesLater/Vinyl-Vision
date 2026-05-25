"""Rate limiting for external APIs to prevent hitting rate limits."""
import asyncio, time
from functools import wraps
from typing import Callable


class RateLimiter:
    """Simple sliding window rate limiter.
    
    Usage:
        limiter = RateLimiter(max_requests=30, window_seconds=60)
        
        async with limiter:  # Acquires slot within timeout
            await some_api_call()
    """
    def __init__(self, max_requests=30, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = []  # timestamps of requests
    
    async def acquire(self):
        """Wait until a slot is available in the rate limit window."""
        now = time.time()
        
        # Remove old requests outside the window
        cutoff = now - self.window_seconds
        self._requests = [t for t in self._requests if t > cutoff]
        
        # Wait if we've hit the limit
        while len(self._requests) >= self.max_requests:
            oldest_in_window = max(self._requests)
            wait_time = oldest_in_window + self.window_seconds - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                # Re-check after sleep (handles clock drift, edge cases)
                cutoff = time.time() - self.window_seconds
                self._requests = [t for t in self._requests if t > cutoff]
        
        self._requests.append(time.time())
    
    async def __aenter__(self):
        """Acquire rate limit slot on entry."""
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """No-op exit."""
        pass


class DiscogsRateLimiter(RateLimiter):
    """Discogs API specific rate limiter.
    
    Discogs limits to ~30 requests/60s for most accounts.
    This uses a slightly more conservative limit (25) to account for burst traffic.
    """
    def __init__(self, token: str = None):
        super().__init__(max_requests=25, window_seconds=60)  # Conservative limit
        self.token = token or ""
    
    @property
    def client_headers(self):
        """Headers for authenticated requests."""
        if self.token:
            return {
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "Vinyl-Vision/1.0 (Discogs)"
            }
        return {"User-Agent": "Vinyl-Vision/1.0"}
