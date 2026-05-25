# Vinyl-Vision 🎵

Drop an album cover photo → get artist, title, Discogs metadata, pricing, and AI-generated notes — all cataloged locally. Supports single albums, batch uploads, and group photos with multiple covers.

## How it works

```
You snap a cover photo → CLIP (embeddings) → Qwen 3.5-9B (vision LLM)
  → MusicBrainz (release lookup) → Discogs (metadata + pricing)
    → Saved to your local catalog
```

Three layers of matching:
1. **CLIP** — instant re-match if you've already cataloged this cover (local embedding index)
2. **Qwen 3.5-9B** — reads the cover art and identifies artist, title, year, label, genre, and writes a fun info blurb
3. **MusicBrainz + Discogs** — enriches with release details, market pricing, tags, and Discogs links

## Features

| Mode | Description |
|------|-------------|
| **📀 Single Album** | Drag-and-drop or tap to photo — identify one album cover at a time |
| **📚 Batch Upload** | Upload 5-10 individual album photos, process in sequence |
| **🖼️ Group Photo** | One photo of multiple albums — Qwen identifies each cover individually |
| **🔍 Qwen Hint** | Optional context field per mode — tell Qwen what to expect (e.g. "jazz from the 60s") before sending the image |

## Prerequisites

- **Python 3.14**
- **LM Studio** with **Qwen 3.5-9B** (the vision model) loaded, API server enabled on port `1234`
- **Discogs API token** (free — sign up at [discogs.com/developers](https://www.discogs.com/developers))
- **HuggingFace token** (free — needed to download the CLIP model)

## Quick Start (native)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API tokens
export DISCOGS_TOKEN="your_discogs_token"
export HF_TOKEN="your_huggingface_token"

# 3. Make sure LM Studio is running with Qwen 3.5-9B on port 1234

# 4. Start the app
uvicorn app:app --host 0.0.0.0 --port 8081

# 5. Open http://localhost:8081
```

## Quick Start (Docker)

```bash
export DISCOGS_TOKEN="your_discogs_token"
export HF_TOKEN="your_huggingface_token"
docker compose up -d
```

The container uses `network_mode: host` so it reaches LM Studio on `localhost:1234`. Your catalog data lives in `./catalog/` and `./images/` on your machine.

## Use it from your phone

1. Connect your phone to the same WiFi as this computer
2. Find your computer's LAN IP (`ip addr show` on Linux)
3. Open `http://<YOUR_IP>:8081` in Safari or Chrome
4. Use any mode — Single Album (auto-opens camera), Batch Upload, or Group Photo

## View logs

All logs print to the terminal where you ran `uvicorn`. Watch it live to see LM Studio responses, MB search results, and any errors.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "LM Studio not running" | Start LM Studio, load Qwen 3.5-9B, enable API server on port 1234 |
| "No matches in MusicBrainz" | Try a clearer photo, or use the Qwen hint field to provide context |
| Permission errors on catalog/ or images/ | `sudo chown -R $(whoami) catalog/ images/` |
| 500 error on upload | Check the terminal log for the specific error |

## Tech Stack

| Component | Model/Tool | Purpose |
|-----------|-----------|---------|
| Vision LLM | **Qwen 3.5-9B** (via LM Studio) | Reads album cover, extracts artist/title/label, generates info commentary |
| Image embeddings | **openai/clip-vit-base-patch32** (via HuggingFace transformers) | Visual similarity search + ranking |
| Music metadata | **MusicBrainz API** | Release lookup, cover art via Cover Art Archive |
| Marketplace data | **Discogs API** | Pricing, tags, genre, format, label, barcode |
| Image processing | **Pillow** | Image handling |
| Frontend | **Vanilla HTML + JS** | Drag-and-drop (or tap-to-photo) interface |

## Project Structure

```
├── app.py              # FastAPI backend (routes, MB search, Discogs client)
├── lm_studio.py        # Qwen 3.5-9B API wrapper
├── image_match.py      # CLIP embeddings, local index, visual ranking
├── app.html            # Frontend (single-page app)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker build
├── docker-compose.yml  # Docker Compose
├── catalog/            # Your catalog data (catalog.json + covers_index.json)
└── images/             # Uploaded cover photos
```
