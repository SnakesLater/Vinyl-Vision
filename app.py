import os, json, io, httpx, asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import image_match
from lm_studio import analyze_cover, check_server

BASE_DIR    = Path(__file__).parent
IMAGES_DIR  = BASE_DIR / "images"
CATALOG_DIR = BASE_DIR / "catalog"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_DIR.mkdir(parents=True, exist_ok=True)

DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN") or ""
MB_UA     = "Vinyl-Vision/1.0 (github.com/SnakesLater/Vinyl-Vision)"
PORT      = int(os.getenv("PORT", 8081))

app = FastAPI(title="Record Catalog API v0.1")

# Stores the last dropped image's CLIP embedding so search-text
# can use it for visual ranking if the user overrides manually.
_last_search_embedding: list | None = None
_last_qwen_result: dict | None = None

# ── MusicBrainz helpers ────────────────────────────────────────────────────────

async def mb_get_release(mbid: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"https://musicbrainz.org/ws/2/release/{mbid}",
            params={"fmt": "json", "inc": "artists+labels"},
            headers={"User-Agent": MB_UA},
        )
        r.raise_for_status()
        return r.json()

async def mb_search_by_text(artist: str, title: str, year: str = "") -> list[dict]:
    safe_a = artist.replace('"', '\\"')
    safe_t = title.replace('"', '\\"')
    query = f'artist:"{safe_a}" AND release:"{safe_t}"'
    print(f"[search-text] query='{query}'", flush=True)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://musicbrainz.org/ws/2/release",
                params={"query": query, "fmt": "json", "limit": 5},
                headers={"User-Agent": MB_UA},
            )
            r.raise_for_status()
            hits = r.json().get("releases", [])
            print(f"[search-text] MB returned {len(hits)} hits", flush=True)
            return hits
    except Exception as e:
        print(f"[search-text] MB error: {e}", flush=True)
        return []

async def check_caa_cover(mbid: str) -> bool:
    """Check if Cover Art Archive has a front image for this release."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.head(
                f"https://coverartarchive.org/release/{mbid}/front-250.jpg")
            return r.status_code == 307
    except Exception as e:
        print(f"[caa] check failed for {mbid}: {e}", flush=True)
        return False

def mb_hits_to_candidates(hits: list[dict], has_cover: set | None = None) -> list[dict]:
    candidates = []
    for hit in hits[:5]:
        mbid = hit.get("id")
        if not mbid:
            continue
        if has_cover is not None and mbid not in has_cover:
            continue
        title = hit.get("title", "Unknown")
        artist_parts = []
        for ac in hit.get("artist-credit", []):
            n = ac.get("name") or ac.get("artist", {}).get("name")
            if n:
                artist_parts.append(n)
        candidates.append({
            "mbid": mbid,
            "title": title,
            "artist": " ".join(artist_parts) if artist_parts else "Unknown",
            "year": (hit.get("date") or "")[:4],
            "cover_url": f"https://coverartarchive.org/release/{mbid}/front-250.jpg",
            "similarity": 0.0,
        })
    return candidates

# ── Discogs client ─────────────────────────────────────────────────────────────
class DiscogsClient:
    def __init__(self, token: str):
        self.token = token
        self.h = {"Authorization": f"Bearer {token}", "User-Agent": "Vinyl-Vision/1.0"}

    async def _get(self, path: str, **kw) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"https://api.discogs.com{path}", headers=self.h, **kw)
            r.raise_for_status()
            return r.json()

    async def search(self, artist: str, title: str) -> dict | None:
        data = await self._get("/database/search", params={
            "q": f"{artist} {title}", "type": "release", "limit": 5})
        results = data.get("results", [])
        return results[0] if results else None

    async def release(self, rid: int) -> dict | None:
        return (await self._get(f"/releases/{rid}", params={"per_page": 1})).get("release")

    async def prices(self, rid: int) -> dict:
        try:
            d = await self._get(f"/marketplace/price_statistics/{rid}")
            return {
                "price_range": {
                    "min": d.get("lowest_price", {}).get("value"),
                    "max": d.get("highest_price", {}).get("value"),
                    "avg": d.get("average_price", {}).get("value"),
                }
            }
        except Exception as e:
            print("prices error:", e)
            return {}

    async def tags(self, rid: int) -> list[str]:
        try:
            return [t["name"] for t in (await self._get(f"/releases/{rid}/tags")).get("tags", [])]
        except Exception as e:
            print("tags error:", e)
            return []

discogs = DiscogsClient(DISCOGS_TOKEN)
if not DISCOGS_TOKEN:
    print("[app] WARNING: DISCOGS_TOKEN not set — Discogs enrichment will be skipped", flush=True)

# ── Catalog ────────────────────────────────────────────────────────────────────
class Catalog:
    def __init__(self, p: Path):
        self.p = p
        self.d: dict = {"albums": []}
        self._load()
    def _load(self):
        if self.p.exists():
            with open(self.p) as f: self.d = json.load(f)
        self.d.setdefault("albums", [])
    def save(self):
        with open(self.p, "w") as f: json.dump(self.d, f, indent=2)
    def add(self, entry: dict) -> dict:
        self.d["albums"].append(entry)
        self.save()
        return entry

catalog = Catalog(CATALOG_DIR / "catalog.json")

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/catalog")
async def get_catalog():
    catalog._load()
    return {"albums": catalog.d["albums"], "total": len(catalog.d["albums"])}

# ── Main image search endpoint ─────────────────────────────────────────────────
@app.post("/search")
async def search(image: UploadFile = File(...)):
    """Drop image → CLIP index → LM Studio → MB → CAA → CLIP rank → candidates"""
    global _last_search_embedding, _last_qwen_result
    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(400, "No image file provided")
    print(f"[search] received {len(img_bytes)} bytes", flush=True)

    embedding = image_match.compute_embedding(img_bytes)
    _last_search_embedding = embedding

    # Tier 1: local index (instant re-match)
    match = image_match.search_index(embedding)
    if match:
        print(f"[search] local index hit: {match['artist']} - {match['title']} ({match['similarity']})", flush=True)
        return {
            "candidates": [match],
            "fallback": False,
            "from_index": True,
        }

    # Tier 2: LM Studio vision model
    if not await check_server():
        print("[search] LM Studio not reachable", flush=True)
        return {"candidates": [], "fallback": True,
                "message": "Image analysis server (LM Studio) is not running. Start it on port 1234, or search manually below."}

    result = await analyze_cover(img_bytes)
    _last_qwen_result = result
    if not result:
        print("[search] LM Studio returned no result", flush=True)
        return {"candidates": [], "fallback": True,
                "message": "Could not identify the album from this image. Try a clearer photo or search manually."}

    artist = result.get("artist", "").strip()
    title  = result.get("title", "").strip()
    if not artist or not title:
        print(f"[search] LM Studio result missing artist/title: {result}", flush=True)
        return {"candidates": [], "fallback": True,
                "message": f"Vision model saw '{result}', but couldn't extract artist and title. Try searching manually."}

    print(f"[search] LM Studio identified: {artist} - {title}", flush=True)

    # Search MusicBrainz (artist + release fields for precision)
    mb_hits = await mb_search_by_text(artist, title)
    if not mb_hits:
        return {"candidates": [], "fallback": True,
                "message": f"Found '{artist} - {title}' in the image, but no matches in MusicBrainz."}

    # CAA cover check
    checks = await asyncio.gather(*[check_caa_cover(h["id"]) for h in mb_hits[:5]])
    has_cover = {mb_hits[i]["id"] for i, ok in enumerate(checks) if ok}
    candidates = mb_hits_to_candidates(mb_hits, has_cover)
    if not candidates:
        return {"candidates": [], "fallback": True,
                "message": "Found matching releases but none have cover art in the archive."}

    # CLIP visual ranking
    ranked = await image_match.rank_candidates(embedding, candidates)
    print(f"[search] returning {len(ranked)} ranked candidates", flush=True)
    return {"candidates": ranked, "fallback": False}

# ── Manual text search (fallback / override) ────────────────────────────────────
@app.post("/search-text")
async def search_text(artist: str = Form(...), title: str = Form(...)):
    if not artist and not title:
        raise HTTPException(400, "Provide at least an artist or title")
    print(f"[search-text] artist='{artist}' title='{title}'", flush=True)

    mb_hits = await mb_search_by_text(artist, title)
    if not mb_hits:
        return {"candidates": []}

    checks = await asyncio.gather(*[check_caa_cover(h["id"]) for h in mb_hits[:5]])
    has_cover = {mb_hits[i]["id"] for i, ok in enumerate(checks) if ok}
    if not has_cover:
        return {"candidates": []}

    candidates = mb_hits_to_candidates(mb_hits, has_cover)

    if _last_search_embedding:
        ranked = await image_match.rank_candidates(_last_search_embedding, candidates)
        return {"candidates": ranked}
    return {"candidates": candidates}

# ── Catalog an album ───────────────────────────────────────────────────────────
@app.post("/upload")
async def upload(
    image: UploadFile = File(...),
    artist: str = Form(...),
    title: str = Form(...),
    mbid: str = Form(""),
    cover_url: str = Form(""),
    condition: str = Form("NM"),
    notes: str = Form(""),
):
    if not (artist and title):
        raise HTTPException(400, "Missing artist or title")
    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(400, "No image file provided")

    suffix = Path(image.filename).suffix or ".jpg"
    safe_name = f"{artist}_{title}{suffix}".replace("/", "_").replace(" ", "_")
    (IMAGES_DIR / safe_name).write_bytes(img_bytes)

    entry = {
        "artist":      artist,
        "title":       title,
        "mbid":        mbid,
        "condition":   condition,
        "notes":       notes,
        "image":       safe_name,
        "cover_url":   cover_url,
        "uploaded_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }

    if mbid:
        try:
            mb_det = await mb_get_release(mbid)
            if mb_det:
                artist_parts = []
                for ac in mb_det.get("artist-credit", []):
                    n = ac.get("name") or ac.get("artist", {}).get("name")
                    if n: artist_parts.append(n)
                mb_artist = " ".join(artist_parts)
                mb_title  = mb_det.get("title", "")

                if mb_artist and mb_title:
                    hit = await discogs.search(mb_artist, mb_title)
                    if hit:
                        did = hit.get("id")
                        dr  = await discogs.release(did)
                        if dr:
                            prices, tags = await asyncio.gather(
                                discogs.prices(did), discogs.tags(did))
                            entry.update({
                                "discogs_id":    did,
                                "discogs_url":   f"https://www.discogs.com/release/{did}",
                                "price_range":   prices.get("price_range", {}),
                                "tags":          tags,
                                "year":          dr.get("year", ""),
                                "format":        dr.get("formats", [{}])[0].get("name", ""),
                                "genre":         [g.get("name") for g in dr.get("genres", [])],
                                "label":         dr.get("labels", [{}])[0].get("name", ""),
                                "barcode":       dr.get("barcode"),
                                "country":       dr.get("country", ""),
                                "catalog_number": dr.get("catalog_number"),
                                "release_date":  dr.get("released"),
                            })
        except Exception as e:
            print(f"Discogs enrichment failed for {mbid}: {e}")

    # Attach Qwen info commentary if artist/title match
    if _last_qwen_result:
        qa = _last_qwen_result.get("artist", "").strip().lower()
        qt = _last_qwen_result.get("title", "").strip().lower()
        if qa == artist.strip().lower() and qt == title.strip().lower():
            entry["info"] = _last_qwen_result.get("info", "")

    catalog.add(entry)

    # Save CLIP embedding to local index for future instant re-matching
    if mbid and safe_name:
        clip_emb = image_match.compute_embedding(img_bytes)
        if not cover_url:
            cover_url = f"https://coverartarchive.org/release/{mbid}/front-250.jpg"
        image_match.add_entry(mbid, clip_emb, artist, title, cover_url)

    return {"status": "cataloged", "album": entry}

# ── Static frontend ─────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(BASE_DIR / "app.html")

@app.get("/app", include_in_schema=False)
async def app_page():
    return FileResponse(BASE_DIR / "app.html")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    svg = (BASE_DIR / "favicon.svg").read_bytes()
    return Response(content=svg, media_type="image/svg+xml")

app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
