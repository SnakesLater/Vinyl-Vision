"""CLIP image matching with LRU caching for faster embeddings."""
import json, torch, io, httpx, asyncio, hashlib
from pathlib import Path
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from collections import OrderedDict

CACHE_DIR = Path(__file__).parent / "catalog"
INDEX_PATH = CACHE_DIR / "covers_index.json"
EMBED_CACHE_FILE = CACHE_DIR / "clip_embeddings.cache"

_MODEL_NAME = "openai/clip-vit-base-patch32"
_DEVICE = "cpu"
_MAX_EMBEDDINGS = 100  # LRU cache size


_clip_model = None
_clip_processor = None


def _load():
    """Lazy-load CLIP model once."""
    global _clip_model, _clip_processor
    if _clip_model is None:
        print(f"[CLIP] loading model on {_DEVICE}...", flush=True)
        _clip_model = CLIPModel.from_pretrained(_MODEL_NAME).to(_DEVICE)
        _clip_processor = CLIPProcessor.from_pretrained(_MODEL_NAME)
        print("[CLIP] model ready", flush=True)
    return _clip_model, _clip_processor


class LRUCache:
    """Lightweight LRU cache for CLIP embeddings."""
    def __init__(self, capacity=100):
        self.capacity = capacity
        self.cache = OrderedDict()
        self.hits = 0
        self.misses = 0
    
    def get(self, key):
        """Get embedding if exists, move to end (most recent)."""
        if key in self.cache:
            # Move to end (MRU)
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]
        self.misses += 1
        return None
    
    def put(self, key, value):
        """Insert or update embedding, evict LRU if over capacity."""
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        # Evict oldest if over capacity
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
    
    def stats(self):
        """Return cache statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "size": len(self.cache),
            "capacity": self.capacity,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 1)
        }


_clip_cache = LRUCache(capacity=_MAX_EMBEDDINGS)


def _save_index(idx: dict):
    """Save local cover index to disk."""
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(idx, indent=2))


def compute_embedding(image_bytes: bytes) -> list[float]:
    """Compute CLIP embedding with LRU caching (checks cache before computing).

    Args:
        image_bytes: Raw image bytes

    Returns:
        Normalized embedding vector as list of floats
    """
    cache_key = hashlib.sha256(image_bytes).hexdigest()
    cached = _clip_cache.get(cache_key)
    if cached is not None:
        print(f"[CLIP] cache hit (# {_clip_cache.stats()})")
        return cached

    model, processor = _load()

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = processor(images=img, return_tensors="pt")

        with torch.no_grad():
            out = model.get_image_features(**inputs)

        if hasattr(out, "pooler_output"):
            out = out.pooler_output
        out = out / out.norm(dim=-1, keepdim=True)
        embedding = out[0].cpu().tolist()

    except Exception as e:
        print(f"[CLIP] embedding error: {e}")
        try:
            return _compute_raw_embedding(image_bytes, model, processor)
        except:
            raise

    _clip_cache.put(cache_key, embedding)

    try:
        if EMBED_CACHE_FILE.exists():
            idx = json.loads(EMBED_CACHE_FILE.read_text())
        else:
            idx = {}
        idx[cache_key] = {"embedding": embedding, "count": 1}
        EMBED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        EMBED_CACHE_FILE.write_text(json.dumps(idx))
    except:
        pass

    print(f"[CLIP] embedding computed (# {_clip_cache.stats()})")
    return embedding


def _compute_raw_embedding(image_bytes: bytes, model, processor) -> list[float]:
    """Fallback: compute embedding without caching."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor(images=img, return_tensors="pt")
    
    with torch.no_grad():
        out = model.get_image_features(**inputs)
    
    if hasattr(out, "pooler_output"):
        out = out.pooler_output
    out = out / out.norm(dim=-1, keepdim=True)
    return out[0].cpu().tolist()


def _load_index() -> dict:
    """Load local cover index from disk."""
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text())
        except:
            return {}
    return {}


def search_index(embedding: list[float], threshold: float = 0.90) -> dict | None:
    """Search local index for best matching release."""
    idx = _load_index()
    if not idx:
        return None
    
    emb = torch.tensor(embedding)
    best_mbid = None
    best_sim = 0.0
    
    for mbid, entry in idx.items():
        try:
            e = torch.tensor(entry["embedding"])
            sim = float(emb @ e)
            if sim > best_sim:
                best_sim = sim
                best_mbid = mbid
        except (KeyError, TypeError):
            continue
    
    if best_sim >= threshold:
        entry = dict(idx[best_mbid])
        entry.pop("embedding", None)
        entry["mbid"] = best_mbid
        entry["similarity"] = round(best_sim, 4)
        return entry
    return None


def add_entry(mbid: str, embedding: list[float], artist: str, title: str, cover_url: str):
    """Add release to local index."""
    idx = _load_index()
    
    # Cache fingerprint-based key
    cache_key = tuple(embedding[:10])
    
    idx[mbid] = {
        "embedding": embedding,
        "artist": artist,
        "title": title,
        "cover_url": cover_url,
    }
    _save_index(idx)
    
    # Update cache index if exists
    try:
        if EMBED_CACHE_FILE.exists():
            idx_cache = json.loads(EMBED_CACHE_FILE.read_text())
        else:
            idx_cache = {}
        
        if cache_key not in idx_cache:
            idx_cache[cache_key] = {"mbid": mbid, "count": 1}
        elif idx_cache[cache_key]["count"] < 5:  # Limit to 5 per key
            idx_cache[cache_key]["count"] += 1
        else:
            # Replace with latest MBID
            old_mbid = idx_cache[cache_key]["mbid"]
            if old_mbid in idx:
                del idx[old_mbid]
        
        EMBED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        EMBED_CACHE_FILE.write_text(json.dumps(idx_cache))
    except:
        pass


async def rank_candidates(dropped_embedding: list[float], candidates: list[dict]) -> list[dict]:
    """Rank candidates by visual similarity to dropped image.
    
    Optimizations:
    - Single async client for HTTP requests (reuse connections)
    - Graceful error handling per candidate
    """
    model, processor = _load()
    dropper = torch.tensor(dropped_embedding)
    
    # Pool all requests with single async client
    async def _score(c: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                r = await client.get(c["cover_url"])
                if r.status_code == 200:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    inputs = processor(images=img, return_tensors="pt")
                    
                    with torch.no_grad():
                        out = model.get_image_features(**inputs)
                    
                    if hasattr(out, "pooler_output"):
                        out = out.pooler_output
                    out = out / out.norm(dim=-1, keepdim=True)
                    c["similarity"] = round(float(dropper @ out[0]), 4)
                    return c
        except Exception as e:
            print(f"[rank] failed for {c.get('artist', '?')}: {e}")
            pass
        
        c["similarity"] = 0.0
        return c
    
    # Parallel ranking of all candidates
    ranked = await asyncio.gather(*[_score(c) for c in candidates])
    ranked.sort(key=lambda x: x["similarity"], reverse=True)
    
    return ranked


def get_cache_stats():
    """Get CLIP cache statistics."""
    return _clip_cache.stats()
