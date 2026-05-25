"""LM Studio client with exponential backoff retry for Qwen integration."""
import json, base64, httpx, re, asyncio
from typing import Optional

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
_MODEL = "qwen/qwen3.5-9b"
_TIMEOU_T = 300
_MAX_RETRIES = 3
_BACKOFF_FACTOR = 2.0


def _detect_mime(header: bytes) -> str:
    """Detect image MIME from bytes."""
    if header[:4] == b'\x89PNG':
        return "image/png"
    if header[:4] in (b'RIFF',):
        return "image/webp"
    return "image/jpeg"


QUICK_PROMPT = (
    'Identify this album by its visual design. '
    'Output ONLY valid JSON with artist and title:\n'
    '{"artist": "...", "title": "..."}'
)

FULL_PROMPT = (
    'Identify this album by its visual design, artwork style, colors, imagery, and composition. '
    'Many album covers use stylized, hand-drawn, or non-standard text that is hard to read. '
    'Rely PRIMARILY on the visual style of the artwork — text on the cover is secondary confirmation.\n\n'
    'Be aware of edge cases:\n'
    '- Multiple artists / "feat." / various artists — put the main artist in "artist"\n'
    '- Compilations, EPs, singles, live albums, soundtracks — note the type\n'
    '- If you know the Discogs release ID, include it in discogs_id (otherwise omit or set to 0)\n'
    '- Estimate a rough fair-to-good condition market price range in price_estimate\n\n'
    'Respond ONLY with valid JSON — no markdown, no backticks, no extra text:\n'
    '{"artist": "Artist Name", "title": "Album Title", '
    '"year": 1975, "label": "Record Label", '
    '"genre": ["Rock", "Pop"], '
    '"type": "album", '
    '"info": "A short interesting paragraph about this album", '
    '"discogs_id": 0, "price_estimate": "Estimated price range"}'
)


class QwenClient:
    """LM Studio client with retry logic for robust AI calls.
    
    Features:
    - Exponential backoff on connection errors
    - Timeout handling
    - JSON parsing cleanup
    """
    def __init__(self, model: str = _MODEL, timeout: int = _TIMEOU_T):
        self.model = model
        self.timeout = timeout
    
    async def _call(self, prompt: str, max_tokens: int, image_bytes: bytes) -> Optional[dict]:
        """Make single API call with error handling."""
        try:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            mime = _detect_mime(image_bytes[:4])
            
            payload = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(LM_STUDIO_URL, json=payload)
                r.raise_for_status()
                
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            
            # Clean up markdown code blocks
            content = re.sub(r'```(?:json)?\s*', '', content).strip()
            content = re.sub(r'\s*```\s*$', '', content).strip()
            
            return json.loads(content)
            
        except httpx.HTTPError as e:
            print(f"[LM] HTTP error (will retry): {e.response.status_code if e.response else 'unknown'}")
            raise
        except Exception as e:
            print(f"[LM] call failed: {type(e).__name__}: {e}")
            return None
    
    async def analyze(self, prompt: str, max_tokens: int, image_bytes: bytes) -> Optional[dict]:
        """Call Qwen with retry logic and exponential backoff.
        
        Args:
            prompt: The prompt to send to Qwen
            max_tokens: Maximum tokens for response
            image_bytes: Image bytes to analyze
            
        Returns:
            Parsed JSON result or None if all retries failed
        """
        last_error = None
        
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                print(f"[LM] Qwen attempt {attempt}/{_MAX_RETRIES}...", flush=True)
                return await self._call(prompt, max_tokens, image_bytes)
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    # Exponential backoff (2s, 4s, 8s...)
                    wait_time = _BACKOFF_FACTOR ** attempt * 0.5
                    print(f"[LM] retrying in {wait_time:.1f}s (attempt {attempt}/{_MAX_RETRIES})...", flush=True)
                    await asyncio.sleep(wait_time)
                else:
                    print(f"[LM] exhausted retries ({_MAX_RETRIES}), last error: {e}")
        
        return None


# ─── Module-level convenience functions ─────────────────────────────────────────

async def analyze_cover(image_bytes: bytes) -> dict | None:
    """Quick ID: artist + title only (fast, ~15-30s)."""
    client = QwenClient()
    return await client.analyze(QUICK_PROMPT, 1536, image_bytes)


async def analyze_cover_full(image_bytes: bytes) -> dict | None:
    """Full enrichment: year, label, genre, type, info, discogs_id, price_estimate."""
    client = QwenClient()
    return await client.analyze(FULL_PROMPT, 3072, image_bytes)

