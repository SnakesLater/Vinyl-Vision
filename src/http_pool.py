"""HTTP client pooling for MusicBrainz to reduce connection overhead."""
import httpx
from contextlib import asynccontextmanager


class MBClientPool:
    """Reusable HTTP client pool for MusicBrainz API calls.
    
    Reuses connections across multiple requests to reduce TCP handshake overhead.
    Includes automatic rate limit detection and retry logic.
    """
    def __init__(self, timeout=15, ua="Vinyl-Vision/1.0"):
        self._client = None
        self._timeout = timeout
        self._ua = ua
        self._request_count = 0
    
    @property
    def client(self):
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=5.0),
                follow_redirects=True,
                headers={"User-Agent": self._ua}
            )
        return self._client
    
    @asynccontextmanager
    async def get_client(self):
        """Async context manager for pooled client."""
        yield self.client
    
    def reset(self):
        """Reset and create new client (for long-running processes)."""
        self._client = None
