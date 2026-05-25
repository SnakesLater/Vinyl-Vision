"""Image optimization functions for CLIP processing."""
from PIL import Image
import io


def optimize_image_for_clip(img_bytes: bytes) -> bytes:
    """
    Optimize image for CLIP processing:
    - Resize to max 1024px (faster inference, consistent embeddings)
    - Convert to RGB (faster than RGBA/PNG for CLIP)
    - Compress to JPEG quality 90 (reduces memory, CLIP doesn't care about quality)
    
    Args:
        img_bytes: Raw image bytes from request
        
    Returns:
        Optimized JPEG bytes ready for CLIP
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        
        # Resize to max 1024px for consistent processing speed
        w, h = img.size
        ratio = 1024 / max(w, h)
        if ratio < 1.0:
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
        
        # Save as JPEG with optimized quality for CLIP
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"[opt] image optimization failed, using original: {e}")
        return img_bytes
