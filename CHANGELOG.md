# Changelog

All notable changes to this experiment branch.

## [beta-4.0] — 2026-05-26

### Added
- **Retry buttons**: per-result ✏️ Retry button on Batch Upload and Group Photo items; inline context form with Send/Cancel
- **Single album Retry**: 🔁 Retry button alongside hint text field — sends steering context to Qwen for a fresh identification
- **Retry context** (`retry_context`): backend accepts on `/search` and `/batch/multi-photo`; wraps with "you got it wrong, try harder" preamble
- **Strong retry mode**: single album retries use temperature 0.3 for more varied answers + emphatic "don't repeat yourself" prompt
- **Async retries**: batch/group retries run independently — catalog other results and queue multiple retries while others process
- **`RETRY_PROMPT`**: new prompt constant in `lm_studio.py` with `{context}` placeholder

### Changed
- **Removed Tier 1 CLIP index shortcut**: every search now always runs Qwen → MB → CAA → ranking. No instant re-matches from local index.
- **Max tokens**: all Qwen calls bumped to **20000** (was 1536/3072/2048)
- **LM Studio timeout**: 300s → **600s**
- **Qwen prompts**: `QUICK_PROMPT` and `MULTI_PROMPT` rewritten with stronger "visual-first, be thorough" language
- **`QwenClient`**: now accepts `temperature` parameter (default 0.1)
- **Singe mode UX**: hint text is now a flex row with Retry button; Retry disabled until an image is loaded
- **Batch result indexing**: fixed bug where `catalogBatchResult()` used subset index instead of original file index — Catalog grabbed wrong file if a prior file had errored
- **Version badge**: v3 → v4
- **API docstring**: cleaned up (removed outdated Phase 1/2 references)

### Fixed
- **`RETRY_PROMPT.format()` crash**: user steering text with `{` or `}` chars would crash with `KeyError` — now escaped before `.format()`
- **Wasteful `QwenClient()` creation**: strong retry no longer creates a default client only to immediately replace it
- **Qwen returning list instead of dict**: `analyze_cover()` now checks for `isinstance(result, list)` and extracts the first element (Qwen sometimes wraps JSON in an array)

### Files modified
- `app.py`
- `lm_studio.py`
- `app.html`
- `CHANGELOG.md`

## [beta-3.0] — 2026-05-25

### Added
- **Qwen hint input**: optional text field per search mode — appended to Qwen prompt as context (e.g. "jazz albums from the 60s")
- **Group Photo mode**: dedicated tab for uploading a single photo of multiple albums; Qwen identifies each cover, results shown inline with Catalog buttons
- **Batch Upload mode**: dedicated tab with file picker, per-file progress, inline results with Catalog buttons
- **Version badge**: small `v3` indicator next to subtitle

### Changed
- **UI rewrite**: new 2-level tab structure — top tabs `[Search] [Catalog]`, sub-tabs `[Single Album] [Batch Upload] [Group Photo]`
- **Batch/Group Photo**: moved out of modal into dedicated page sections, visible immediately (no need to fail single upload first)
- **Catalog form modal**: form fields wrapped in `#catalog-form-fields` for clean separation from album detail view
- **Modal close**: uses class toggle only (`.open`), no inline `style.display` conflict

### Fixed
- **`showDetail()`**: was referencing non-existent `#detail-content` element — now writes to the actual element
- **Duplicate IDs**: `#progress-track` / `#progress-fill` no longer duplicated across single/batch flows
- **Catalog button**: was inside hidden modal with no way to trigger — replaced with inline "📋 Open Catalog Form" button

### Backend
- **`hint: str = Form("")`** added to `/search`, `/batch/upload`, `/batch/multi-photo`
- **`analyze_cover()` and `analyze_cover_multi()`** accept optional hint, prepend as `"Context: {hint}\n\n{prompt}"`

### Files modified
- `app.html` (full rewrite)
- `app.py`
- `lm_studio.py`
- `CHANGELOG.md`

## [experiment] — 2026-05-25

### Added
- **Camera capture**: `capture="environment"` on file input — mobile opens rear camera directly
- **Fuzzy MB fallback**: when strict `artist:"X" AND release:"Y"` query returns 0 hits, retry with a general `Q=X+Y` query for misspelling tolerance
- **Qwen confidence field**: `confidence: "high" | "medium" | "low"` flags uncertain identifications; yellow warning banner shown for low confidence
- **Qwen type field**: `type: "album" | "single" | "ep" | "compilation" | "live" | "soundtrack"` — noted in JSON schema
- **Qwen price_estimate + discogs_id**: Qwen outputs rough fair-to-good market price range and Discogs release ID (if known)
- **AI Suggestion card**: when Qwen identifies an album, an "AI Recognition" card appears in search results alongside MB candidates
- **Catalog from AI**: albums can be cataloged directly from Qwen's data (artist, title, year, label, genre, info, price_estimate) without needing a MusicBrainz match
- **AI metadata in catalog entry**: year, label, genre, info, discogs_id, price_estimate are stored in the catalog JSON

### Changed
- **Qwen prompt reworked**: PRIMARY identification is now by visual design/artwork/imagery — text extraction is secondary (handles stylized/non-standard fonts)
- **Image resize quality**: 92 → 95 for sharper text in Qwen images
- **LM Studio timeout**: 120s → 300s for slow hardware
- **LM Studio max_tokens**: 4096 → 1024 → **3072** (Qwen 3.5 uses reasoning tokens internally; 1024 wasn't enough for thinking + JSON output)
- **Progress bar**: fixed timer steps replaced with "Waiting on AI analysis..." + pulsing animation after 4s
- **Fallback form**: pre-filled with Qwen's artist/title when MB finds no match
- **CLIP ranking**: `follow_redirects=True` so CAA cover images download properly (was stuck at similarity 0.0)

### Removed
- **`check_server()`**: 3s health check gate removed — Qwen is called directly; connectivity check only runs *after* Qwen fails, giving a better error message

### Files modified
- `app.html`
- `app.py`
- `lm_studio.py`
- `image_match.py`
- `main.py`
- `src/rate_limiter.py`
- `CHANGELOG.md`

### Fixed
- **CRITICAL**: `/search` endpoint was broken — `batch_upload` was accidentally inserted between the `@app.post("/search")` decorator and the search function body, leaving the endpoint with only a docstring. Refactored the search pipeline into an `_process_image()` helper used by both `/search` and `/batch/upload`.
- **`lm_studio.py`**: removed duplicate `analyze_cover` / `analyze_cover_full` definitions (defined twice), fixed backoff variable typo `BACKOFF_FACTOR` → `_BACKOFF_FACTOR`
- **`image_match.py`**: CLIP cache now hashes input bytes with SHA256 and checks the cache *before* computing the embedding (was computing first, then checking — defeating the purpose)
- **`src/rate_limiter.py`**: added missing `import asyncio` (line 34 called `asyncio.sleep()` without importing it)

### Removed
- **Dead files**: `lm_studio_functions_addition.py`, `model_router.py`, `models.yaml`, `start_api.py` — unused/abandoned code

### Changed
- **`/batch/upload` endpoint**: now runs the same full pipeline as `/search` via `_process_image()` (was calling `analyze_cover()` directly and trying to find "candidates" in the raw LM Studio result, which never worked)

### Fixed
- **CAA cover check always returning False**: `MBClientPool` has `follow_redirects=True`, so httpx follows CAA's 307 redirect and returns 200. `check_caa_cover()` was checking for `status == 307` → always False. Changed to `status == 200`. This broke cover art detection for every album not already in the local index.

### How to revert to stable main
```bash
git checkout main
git branch -D experiment
```
