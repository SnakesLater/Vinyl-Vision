import json, torch, io, httpx, asyncio
from pathlib import Path
from PIL import Image
from transformers import CLIPProcessor, CLIPModel

CACHE_DIR = Path(__file__).parent / "catalog"
INDEX_PATH = CACHE_DIR / "covers_index.json"
_MODEL_NAME = "openai/clip-vit-base-patch32"

_clip_model = None
_clip_processor = None

DEVICE = "cpu"

def _load():
    global _clip_model, _clip_processor
    if _clip_model is None:
        print(f"[image_match] loading CLIP on {DEVICE}...", flush=True)
        _clip_model = CLIPModel.from_pretrained(_MODEL_NAME).to(DEVICE)
        _clip_processor = CLIPProcessor.from_pretrained(_MODEL_NAME)
        print("[image_match] CLIP ready", flush=True)
    return _clip_model, _clip_processor

def compute_embedding(image_bytes: bytes) -> list[float]:
    model, processor = _load()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor(images=img, return_tensors="pt")
    with torch.no_grad():
        out = model.get_image_features(**inputs)
    if hasattr(out, "pooler_output"):
        out = out.pooler_output
    out = out / out.norm(dim=-1, keepdim=True)
    return out[0].cpu().tolist()

def _load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text())
    return {}

def _save_index(idx: dict):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(idx, indent=2))

def search_index(embedding: list[float], threshold: float = 0.90) -> dict | None:
    idx = _load_index()
    if not idx:
        return None
    emb = torch.tensor(embedding)
    best_mbid = None
    best_sim = 0.0
    for mbid, entry in idx.items():
        e = torch.tensor(entry["embedding"])
        sim = float(emb @ e)
        if sim > best_sim:
            best_sim = sim
            best_mbid = mbid
    if best_sim >= threshold:
        entry = dict(idx[best_mbid])
        entry.pop("embedding", None)
        entry["mbid"] = best_mbid
        entry["similarity"] = round(best_sim, 4)
        return entry
    return None

def add_entry(mbid: str, embedding: list[float], artist: str, title: str, cover_url: str):
    idx = _load_index()
    idx[mbid] = {
        "embedding": embedding,
        "artist": artist,
        "title": title,
        "cover_url": cover_url,
    }
    _save_index(idx)

async def rank_candidates(dropped_embedding: list[float], candidates: list[dict]) -> list[dict]:
    model, processor = _load()
    dropper = torch.tensor(dropped_embedding)

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
        except:
            pass
        c["similarity"] = 0.0
        return c

    ranked = await asyncio.gather(*[_score(c) for c in candidates])
    ranked.sort(key=lambda x: x["similarity"], reverse=True)
    return ranked
