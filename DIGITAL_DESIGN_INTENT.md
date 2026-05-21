# 🎵 Record Catalog Backend - Design Intent Document

## 📊 **Project Status**
- **Version:** v0.02-beta
- **Last Updated:** 2026-05-19
- **Current Branch:** feature/musicbrainz
- **API Endpoint:** POST /upload

---

## 🎯 **Purpose**

Automatically enrich album image uploads with comprehensive Discogs metadata through **MusicBrainz image recognition** to create a searchable catalog of vinyl records, CDs, cassettes, and other media formats without manual research.

---

## 📋 **Core Workflow (Magic Option B)**

```
User Action: Drag-and-drop 1:1 album art image
    ↓
Frontend: POST /upload (multipart/form-data)
    Payload:
      - image: Uploaded album art file
      - artist: Fallback text if MB search fails
      - title: Fallback text if MB search fails
      - condition: User-selected from Discogs standards
      - use_musicbrainz: boolean (defaults to true)
    ↓
Backend Processing:
  1. Save image to /app/images/
  2. Send image to MusicBrainz /ws/2/is/image endpoint
  3. Receive matching releases from MusicBrainz
  4. Fetch full release details from Discogs API v3
  5. Extract ALL metadata:
     - Artist & album title (with year, label)
     - Format (vinyl/CD/cassette)
     - Genre & country
     - Discogs condition grades
     - Current market prices (avg, min, max)
     - Price history data
     - Community-verified tags
  6. User provides condition selection
  7. Save to unified catalog file
    ↓
Frontend: Display album card with all metadata
```

---

## 🛠️ **Technical Architecture**

### **Backend Services:**
- **Framework:** FastAPI + Uvicorn
- **Image Processing:** PIL/Pillow (resize, crop, save)
- **MusicBrainz API:** is_image endpoint (image-based search)
- **Discogs API:** v3 REST API (metadata extraction)
- **Storage:** JSON filesystem at `/app/catalog/catalog.json`
- **Port:** 8080

### **Frontend Contract:**
- **Method:** POST `/upload`
- **Content-Type:** multipart/form-data
- **Payload Fields:**
  - `image`: File upload
  - `artist`: String (fallback)
  - `title`: String (fallback)
  - `condition`: String (from Discogs dropdown)
  - `use_musicbrainz`: Boolean (optional)
- **Expected Response:** JSON with album metadata

---

## 🎨 **Discogs Condition Standards (UI Dropdown)**

Based on Discogs community standards:

| Grade | Display Name | Usage |
|-------|-------------|-------|
| Mint (Sealed) | Mint (Sealed) | Factory sealed |
| Mint (NM+) | Near Mint | Like new |
| Mint | Mint | Flawless |
| Very Near Mint | Very Near Mint | Excellent |
| Excellent | Excellent | Minor wear |
| Very Good | VG+ | Light wear |
| Good | Good | Noticeable wear |
| Fair | Fair | Heavy wear |
| Poor | Poor | Poor condition |

**UI Dropdown Options:**
- Mint (Sealed)
- Near Mint
- Very Near Mint
- Excellent
- Very Good
- Good
- Fair
- Poor

---

## 💾 **Catalog Storage Structure**

**File:** `/app/catalog/catalog.json`

```json
{
  "albums": [
    {
      "artist": "Artist Name",
      "title": "Album Title",
      "full_title": "Artist Name - Album Title",
      "year": 1999,
      "label": "Label Name",
      "format": "Vinyl",
      "country": "US",
      "genre": ["Rock", "Alternative"],
      "barcode": "123456789",
      "release_date": "1999-01-01",
      "catalog_number": "ABC-123",
      "description": "Release description...",
      "pressing_info": {
        "matrix": "XYZ-999",
        "total_pressings": 5000,
        "run": "First run"
      },
      "conditions_available": {
        "Mint (Sealed)": [...],
        "Near Mint": [...]
      },
      "price_summary": {
        "average_price": 42.99,
        "price_range": {
          "min": 12.50,
          "max": 150.00
        }
      },
      "tags": ["Original Run", "Remaster", "Reissue"],
      "conditions": ["Mint"],  // User-selected
      "image": "album_art.jpg",
      "image_url": "/images/album_art.jpg",
      "discogs_url": "https://www.discogs.com/release/12345",
      "musicbrainz_url": "https://musicbrainz.org/release/abc123",
      "status": "active",
      "uploaded_at": "2026-05-19T15:30:00Z"
    }
  ]
}
```

---

## 📝 **Version History**

### **v0.01 (Current Baseline)**
- Simple FastAPI endpoints
- Missing MusicBrainz integration
- Uses text search fallback
- Basic catalog storage

### **v0.02 (Feature Complete)**
- MusicBrainz image search integration
- Discogs v3 API metadata extraction
- Condition dropdown from Discogs standards
- Unified catalog storage
- Price range data
- Full pressing info

### **v0.03 (Future)**
- SAM integration for photo cropping
- User photo upload with auto-detection
- Enhanced UI for condition selection
- Price tracking over time

---

## 🔑 **Configuration**

**Discogs API Token:** Set via `DISCOGS_TOKEN` environment variable (get yours at [discogs.com/developers](https://www.discogs.com/developers))

**MusicBrainz API:** Public endpoint (no token needed)

**Image Storage:** `/app/images/`

**Catalog File:** `/app/catalog/catalog.json`

---

## ✅ **Success Criteria**

- ✅ Upload image → auto-identify via MusicBrainz
- ✅ Extract ALL Discogs metadata (tags, prices, condition)
- ✅ Display condition dropdown for user selection
- ✅ Save to unified JSON catalog
- ✅ Frontend displays easy-to-read album cards
- ✅ No manual Discogs research needed

---

## 🚀 **Next Steps**

1. **Copy container** from Eve machine (preserving originals)
2. **Build** with MusicBrainz integration
3. **Test** end-to-end flow
4. **Deploy** to production
5. **Add** enhanced UI features

---

**End of Design Document v0.02**
