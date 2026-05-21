import json, base64, httpx, re

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
_MODEL = "qwen/qwen3.5-9b"
_TIMEOUT = 120

def _detect_mime(header: bytes) -> str:
    if header[:4] == b"\x89PNG":
        return "image/png"
    if header[:4] in (b"RIFF",):
        return "image/webp"
    return "image/jpeg"

PROMPT = (
    'Look at this album cover. Respond ONLY with valid JSON: '
    '{"artist": "...", "title": "...", "year": ..., "label": "...", '
    '"genre": ["..."], '
    '"info": "A fun short paragraph about this album - notable tracks, '
    'what was happening with the artist at the time, and interesting bits '
    'about its place in music history."}'
)

async def analyze_cover(image_bytes: bytes) -> dict | None:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = _detect_mime(image_bytes[:4])
    payload = {
        "model": _MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(LM_STUDIO_URL, json=payload)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            content = re.sub(r'```(?:json)?\s*', '', content).strip()
            content = re.sub(r'\s*```\s*$', '', content).strip()
            return json.loads(content)
    except Exception as e:
        print(f"[lm_studio] error: {e}", flush=True)
        return None

async def check_server() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("http://localhost:1234/v1/models")
            return r.status_code == 200
    except:
        return False
