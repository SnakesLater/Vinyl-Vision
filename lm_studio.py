import json, base64, httpx, re

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
_MODEL = "qwen/qwen3.5-9b"
_TIMEOUT = 300

def _detect_mime(header: bytes) -> str:
    if header[:4] == b"\x89PNG":
        return "image/png"
    if header[:4] in (b"RIFF",):
        return "image/webp"
    return "image/jpeg"

PROMPT = (
    'Identify this album cover. Extract the EXACT artist name and EXACT album title '
    'if readable on the cover art. If there is no text, identify by visual style, '
    'era, and genre cues instead.\n\n'
    'Be aware of edge cases:\n'
    '- Multiple artists / "feat." / various artists — put the main artist in "artist"\n'
    '- Compilations, EPs, singles, live albums, soundtracks — note the type\n'
    '- If unsure about any field, set confidence to "low"\n\n'
    'Respond ONLY with valid JSON — no markdown, no backticks, no extra text:\n'
    '{"artist": "Artist Name", "title": "Album Title", '
    '"year": 1975, "label": "Record Label", '
    '"genre": ["Rock", "Pop"], '
    '"type": "album", "confidence": "high", '
    '"info": "A short interesting paragraph about this album"}'
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
        "max_tokens": 1024,
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


