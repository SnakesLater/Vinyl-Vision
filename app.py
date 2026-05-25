"""
Vinyl-Vision Production API v0.2 — Optimized Record Catalog System

Optimizations Applied (Phase 1 & 2):
• Image preprocessing for CLIP consistency and efficiency
• CLIP LRU caching (100 entries, tracks hits/misses)
• LM Studio retry with exponential backoff
• HTTP client pooling for MusicBrainz connections
• Discogs rate limiting to prevent API blocks
"""

import os, json, io, httpx, asyncio, datetime
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

import image_match
from lm_studio import analyze_cover, analyze_cover_full, analyze_cover_multi
from src.optimize_images import optimize_image_for_clip
from src.http_pool import MBClientPool
from src.rate_limiter import DiscogsRateLimiter


# ─── Configuration ──────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
IMAGES_DIR  = BASE_DIR / "images"
CATALOG_DIR = BASE_DIR / "catalog"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_DIR.mkdir(parents=True, exist_ok=True)

DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN") or "CwfszcWdoSUrWhnvaAhzyUBWbfbXKdlaMAVtVGmd"
HF_TOKEN  = os.getenv("HF_TOKEN") or ""
MB_UA     = "Vinyl-Vision/1.0 (github.com/SnakesLater/Vinyl-Vision)"
PORT      = int(os.getenv("PORT", 8081))

app = FastAPI(title="Record Catalog API v0.2")


# ─── Global State & HTTP Pools ──────────────────────────────────────────────────
_last_search_embedding: list | None = None
_last_qwen_result: dict | None = None

mb_client_pool = MBClientPool(timeout=15, ua=MB_UA)


def get_discogs_limiter():
    """Get thread-safe Discogs rate limiter."""
    if not hasattr(app, '_discogs_limiter'):
        app._discogs_limiter = DiscogsRateLimiter(token=DISCOGS_TOKEN)
    return app._discogs_limiter


# ─── MusicBrainz Helpers ────────────────────────────────────────────────────────

@asynccontextmanager
async def get_mb_client():
    """Async context manager for pooled MB client."""
    async with mb_client_pool.get_client() as c:
        yield c


async def mb_get_release(mbid: str) -> dict | None:
    """Fetch release details from MusicBrainz using pooled client."""
    try:
        async with get_mb_client() as c:
            r = await c.get(
                f"https://musicbrainz.org/ws/2/release/{mbid}",
                params={"fmt": "json", "inc": "artists+labels"},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        print(f"[MB] {e.response.status_code}: {e.response.text[:100]}")
        if e.response.status_code == 429:
            await asyncio.sleep(1)
            raise
    except Exception as e:
        print(f"[MB] error: {e}")
        return None


async def mb_search_by_text(artist: str, title: str) -> list[dict]:
    """Search MusicBrainz by artist and release text."""
    safe_a = artist.replace('\"', '\\\"')
    safe_t = title.replace('\"', '\\\"')
    query = f'artist:"{safe_a}" AND release:"{safe_t}"'
    print(f"[search-text] query='{query}'", flush=True)
    
    try:
        async with get_mb_client() as c:
            r = await c.get(
                "https://musicbrainz.org/ws/2/release",
                params={"query": query, "fmt": "json", "limit": 5},
                headers={"User-Agent": MB_UA},
            )
            r.raise_for_status()
            hits = r.json().get("releases", [])
            
            # Fuzzy fallback for misspellings
            if not hits:
                fuzzy_q = f'{safe_a} {safe_t}'
                print(f"[search-text] strict miss, trying fuzzy: '{fuzzy_q}'", flush=True)
                r2 = await c.get(
                    "https://musicbrainz.org/ws/2/release",
                    params={"query": fuzzy_q, "fmt": "json", "limit": 10},
                    headers={"User-Agent": MB_UA},
                )
                r2.raise_for_status()
                hits = r2.json().get("releases", [])
            
            return hits
    except Exception as e:
        print(f"[search-text] MB error: {e}")
        return []


async def check_caa_cover(mbid: str) -> bool:
    """Check if Cover Art Archive has a front image for this release."""
    try:
        async with get_mb_client() as c:
            r = await c.head(
                f"https://coverartarchive.org/release/{mbid}/front-250.jpg")
            return r.status_code == 200
    except Exception as e:
        print(f"[caa] check failed for {mbid}: {e}")
        return False


def mb_hits_to_candidates(hits: list[dict], has_cover: set | None = None) -> list[dict]:
    """Convert MB search results to candidate list."""
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


# ─── Discogs Client with Rate Limiting ──────────────────────────────────────────
class DiscogsClient:
    def __init__(self, token: str):
        self.token = token
        self._h = {"Authorization": f"Bearer {token}", "User-Agent": "Vinyl-Vision/1.0"}

    async def _get(self, path: str, **kw) -> dict:
        """Make authenticated request with rate limiting."""
        limiter = get_discogs_limiter()
        async with limiter:
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.get(f"https://api.discogs.com{path}", headers=self._h, **kw)
                    r.raise_for_status()
                    return r.json()
            except httpx.HTTPStatusError as e:
                print(f"[Discogs] {e.response.status_code}: {e.response.text[:100]}")
                if e.response.status_code == 429:
                    await asyncio.sleep(2)
                    raise
            except Exception as e:
                print(f"[Discogs] error: {e}")
                raise

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
    print("[app] WARNING: DISCOGS_TOKEN not set — Discogs enrichment will be skipped")


# ─── Catalog ────────────────────────────────────────────────────────────────────
class Catalog:
    def __init__(self, p: Path):
        self.p = p
        self.d: dict = {"albums": []}
        self._load()

    def _load(self):
        if self.p.exists():
            with open(self.p) as f:
                self.d = json.load(f)
        self.d.setdefault("albums", [])

    def save(self):
        with open(self.p, "w") as f:
            json.dump(self.d, f, indent=2)

    def add(self, entry: dict) -> dict:
        self.d["albums"].append(entry)
        self.save()
        return entry


catalog = Catalog(CATALOG_DIR / "catalog.json")


async def _process_image(image_bytes: bytes, hint: str = "") -> dict:
    """Run the full search pipeline on image bytes.

    Returns a dict with keys: candidates, fallback, qwen_meta, message, from_index.
    """
    global _last_search_embedding, _last_qwen_result

    print(f"[search] received {len(image_bytes)} bytes", flush=True)

    image_bytes = optimize_image_for_clip(image_bytes)

    embedding = image_match.compute_embedding(image_bytes)
    _last_search_embedding = embedding

    # Tier 1: local index (instant re-match)
    match = image_match.search_index(embedding)
    if match:
        print(f"[search] local index hit: {match['artist']} - {match['title']} ({match['similarity']})")
        return {"candidates": [match], "fallback": False, "from_index": True}

    # Tier 2: LM Studio vision model — quick ID (artist + title)
    result = await analyze_cover(image_bytes, hint)
    _last_qwen_result = result

    if not result:
        reachable = False
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get("http://localhost:1234/v1/models")
                reachable = r.status_code == 200
        except Exception:
            pass
        msg = ("Image analysis server (LM Studio) is not running. Start it on port 1234, or search manually below."
               if not reachable else
               "Could not identify the album from this image. Try a clearer photo or search manually.")
        print(f"[search] LM Studio returned no result (reachable={reachable})")
        return {"candidates": [], "fallback": True, "message": msg}

    artist = result.get("artist", "").strip()
    title = result.get("title", "").strip()

    if not artist or not title:
        print(f"[search] LM Studio result missing artist/title: {result}")
        return {"candidates": [], "fallback": True,
                "message": f"Vision model saw '{result}', but couldn't extract artist and title."}

    print(f"[search] LM Studio identified: {artist} - {title}")

    qwen_meta = {"artist": artist, "title": title}

    mb_hits = await mb_search_by_text(artist, title)
    if not mb_hits:
        return {"candidates": [], "fallback": True,
                "qwen_meta": qwen_meta,
                "message": f"Qwen suggested '{artist} - {title}', but no matches in MusicBrainz."}

    checks = await asyncio.gather(*[check_caa_cover(h["id"]) for h in mb_hits[:5]])
    has_cover = {mb_hits[i]["id"] for i, ok in enumerate(checks) if ok}
    candidates = mb_hits_to_candidates(mb_hits, has_cover)
    if not candidates:
        return {"candidates": [], "fallback": True,
                "qwen_meta": qwen_meta,
                "message": "Found matching releases but none have cover art in the archive."}

    ranked = await image_match.rank_candidates(embedding, candidates)
    print(f"[search] returning {len(ranked)} ranked candidates")

    resp = {"candidates": ranked, "fallback": False}
    if qwen_meta:
        resp["qwen_meta"] = qwen_meta
    return resp


# ─── Routes ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/catalog")
async def get_catalog():
    catalog._load()
    return {"albums": catalog.d["albums"], "total": len(catalog.d["albums"])}


@app.post("/search")
async def search(image: UploadFile = File(...), hint: str = Form("")):
    """Drop image -> CLIP index -> LM Studio -> MB -> CAA -> CLIP rank -> candidates"""
    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(400, "No image file provided")
    return await _process_image(img_bytes, hint)


@app.post("/batch/upload")
async def batch_upload(files: list[UploadFile] = File(...), hint: str = Form("")):
    """Run the full search pipeline on multiple images."""
    if len(files) < 1 or len(files) > 15:
        raise HTTPException(400, "Batch must have 1-15 files")

    results = []
    for idx, f in enumerate(files, 1):
        try:
            img_bytes = await f.read()
            result = await _process_image(img_bytes, hint)
            results.append({
                "index": idx,
                "filename": f.filename,
                "status": "success",
                "result": result,
            })
        except Exception as e:
            print(f"[batch] error processing {f.filename}: {e}")
            results.append({
                "index": idx,
                "filename": f.filename,
                "status": "error",
                "error": str(e)[:200],
            })

    return {"total": len(files), "results": results}


@app.post("/batch/multi-photo")
async def batch_multi_photo(image: UploadFile = File(...), hint: str = Form("")):
    """Identify multiple albums from a single photo using Qwen vision."""
    img_bytes = await image.read()
    if not img_bytes:
        raise HTTPException(400, "No image file provided")

    albums = await analyze_cover_multi(img_bytes, hint)
    if not albums:
        return {"total": 0, "results": []}

    print(f"[multi-photo] Qwen found {len(albums)} albums: {albums}", flush=True)

    candidates_by_title = {}

    for a in albums:
        artist = a.get("artist", "").strip()
        title = a.get("title", "").strip()
        key = f"{artist}|{title}"
        if not artist or not title or key in candidates_by_title:
            continue

        mb_hits = await mb_search_by_text(artist, title)
        cover_url = ""
        mbid = ""
        if mb_hits:
            hit = mb_hits[0]
            mbid = hit.get("id", "")
            cover_ok = await check_caa_cover(mbid) if mbid else False
            if cover_ok:
                cover_url = f"https://coverartarchive.org/release/{mbid}/front-250.jpg"

        candidates_by_title[key] = {
            "artist": artist,
            "title": title,
            "mbid": mbid,
            "cover_url": cover_url,
        }

    results = [{"index": i + 1, **v} for i, v in enumerate(candidates_by_title.values())]
    print(f"[multi-photo] returning {len(results)} candidates", flush=True)
    return {"total": len(results), "results": results}


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


@app.post("/upload")
async def upload(
    image: UploadFile = File(...),
    artist: str = Form(...),
    title: str = Form(...),
    mbid: str = Form(""),
    cover_url: str = Form(""),
    condition: str = Form("NM"),
    notes: str = Form(""),
    year: str = Form(""),
    label: str = Form(""),
    genre: str = Form(""),
    info: str = Form(""),
    discogs_id: str = Form(""),
    price_estimate: str = Form(""),
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
        "artist":         artist,
        "title":          title,
        "mbid":           mbid,
        "condition":      condition,
        "notes":          notes,
        "image":          safe_name,
        "cover_url":      cover_url,
        "year":           year,
        "label":          label,
        "genre":          genre,
        "info":           info,
        "discogs_id":     discogs_id,
        "price_estimate": price_estimate,
        "uploaded_at":    datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
    }

    if mbid:
        try:
            mb_det = await mb_get_release(mbid)
            if mb_det:
                artist_parts = []
                for ac in mb_det.get("artist-credit", []):
                    n = ac.get("name") or ac.get("artist", {}).get("name")
                    if n:
                        artist_parts.append(n)
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

    # AI cataloging — enrich with full Qwen metadata
    if not mbid:
        print("[upload] getting full Qwen metadata...", flush=True)
        full = await analyze_cover_full(img_bytes)
        if full:
            print(f"[upload] Qwen enrichment: {full.get('year')} / {full.get('label')}")
            if full.get("year"):    entry["year"] = str(full["year"])
            if full.get("label"):   entry["label"] = full["label"]
            if full.get("genre"):   entry["genre"] = ", ".join(full["genre"]) if isinstance(full["genre"], list) else str(full["genre"])
            if full.get("info"):    entry["info"] = full["info"]
            if full.get("discogs_id"): 
                entry["discogs_id"] = str(full["discogs_id"])
            if full.get("price_estimate"): 
                entry["price_estimate"] = full["price_estimate"]

    catalog.add(entry)

    # Save CLIP embedding to local index for future instant re-matching
    if mbid and safe_name:
        clip_emb = image_match.compute_embedding(img_bytes)
        if not cover_url:
            cover_url = f"https://coverartarchive.org/release/{mbid}/front-250.jpg"
        image_match.add_entry(mbid, clip_emb, artist, title, cover_url)

    return {"status": "cataloged", "album": entry}


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(BASE_DIR / "app.html", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get("/app", include_in_schema=False)
async def app_page():
    return FileResponse(BASE_DIR / "app.html", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    svg = (BASE_DIR / "favicon.svg").read_bytes()
    return Response(content=svg, media_type="image/svg+xml")

app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")


if __name__ == "__main__":
    print(f"Starting Vinyl-Vision API on port {PORT}...")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
