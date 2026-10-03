# AGENTS.md — Frames Studio (ReelMind) architecture source of truth

Read this before answering ANY question about what is implemented in this repo.
Statuses below are authoritative: `IMPLEMENTED` = live in production path,
`DEFERRED` = intentionally not implemented yet, `LEGACY` = present but never used.

## Quick status: common questions

| Question | Answer | Status |
|---|---|---|
| Is Qdrant used? | Yes — local server `http://localhost:6333`, collection `reelmind_minilm384_v1` (384-dim, Cosine, 70,051 points). All semantic search, Find Similar, and Explore highlight query it. | IMPLEMENTED |
| Is CLIP used? | **No.** No CLIP model is loaded or called anywhere. Embeddings are text-only `sentence-transformers/all-MiniLM-L6-v2` (384-dim). | NOT IMPLEMENTED |
| What is `scenes-clip-vitb32-laion2b-v1`? | A pre-existing **CLIP ViT-B/32 512-dim** Qdrant collection with 0 points. It is listed in `backend/app/config.py → LEGACY_COLLECTIONS` and guarded in `services/vectorstore.py` — inspected once, **never written, never deleted**. It belongs to the deferred Image Search phase. | LEGACY (protected) |
| Is Image Search implemented? | No — deferred by design. `backend/app/config.py → IMAGE_VLM_PROVIDER = "disabled"`; `/api/health` reports `image_search: {status: "deferred"}`. CLIP/VLM wiring goes here when that phase starts. | DEFERRED |
| Is non-English translation implemented? | Provider abstraction exists (`services/language.py`: Ollama/Noop, pluggable Gemini), but the ~38k-row batch has **not** run. Index is English-only (84k annotations). | DEFERRED (phase 2) |
| Keyword / LIKE search? | Never used in the retrieval path. Search = embed query → Qdrant cosine → SQLite hydration. | rule |

## Stack

- **Frontend** `frontend/` — Next.js 16 (App Router, TS), dev on `http://localhost:3000`, API base `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010`. Pages: `/` (Discover), `/script`, `/story`, `/explore` (Visual Content Universe), `/history`.
- **Backend** `backend/` — FastAPI on `127.0.0.1:8010`, routers in `backend/app/routers/`, services in `backend/app/services/` (`embeddings.py`, `vectorstore.py`, `search.py`, `language.py`, `concepts.py`).
- **Worker** `worker/pipeline.py` — single Python worker, one stage per command (below). Never run model training in the API process.
- **Storage** — SQLite `data/reelmind.db` (122,664 annotations, 2,089 segments, 1,970 ready), media in `media/`, derived caches in `data/` (embeddings shards, reports, `data/explore/universe_v1.json`).
- **Dataset** — `MSR Video Description Corpus.csv` (read-only) + `YouTubeClips/YouTubeClips/*.avi` (`{VideoID}_{Start}_{End}.avi`, filename clock = original YouTube position; segment identity = `video_id:start-end`).

## Key invariants

1. **Embedding model is 384-dim** — read dim from model config, never hardcode; `vectorstore.ensure_collection` raises on dimension mismatch instead of deleting collections.
2. **Never touch `LEGACY_COLLECTIONS`** (`scenes-clip-vitb32-laion2b-v1`).
3. **No cloud APIs** in the runtime path (Clerk/Gemini/etc. only if a future phase explicitly enables them behind the provider abstraction).
4. **Explore universe is cached** — clustering (K-Means k=20 by silhouette + HDBSCAN subclusters + classical MDS layout, seed 42) runs only via `python worker/pipeline.py universe --refresh`; the API serves `data/explore/universe_v1.json` and never recomputes on page load.
5. **Storage on D:** — pip/HF/npm caches redirected to `D:\Epoch_Final\.cache` (C: is nearly full).
6. **Segment-scoped FFmpeg only** — previews/rough cuts are trimmed from source AVIs with re-encoded timestamps; never concatenate raw sources directly.

## Commands

```bash
# pipeline stages (from repo root, Windows)
python worker/pipeline.py ingest|probe|language|embed|index|previews|explore|universe|report
python worker/pipeline.py universe --refresh      # rebuild Explore galaxy cache (~15s)

# API (from backend/)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010

# frontend (from frontend/)
npm run dev                                        # :3000

# tests / eval
pytest tests/test_api.py                           # needs API on :8010
python tests/make_eval_fixture.py && python tests/eval_retrieval.py   # Recall@k / MRR
```

## Where things live

| Feature | Backend | Frontend |
|---|---|---|
| Semantic search / Find Similar | `routers/search.py`, `services/search.py` | `app/page.tsx`, `components/ResultCard.tsx` |
| Script → B-Roll | `routers/script.py` | `app/script/page.tsx` |
| Story / clip editor / rough cut | `routers/story.py`, `worker/stages/roughcut.py` | `app/story/page.tsx` |
| Visual Content Universe | `routers/explore.py` (`/universe*`), `worker/stages/universe.py` | `app/explore/page.tsx` |
| History | `routers/history.py` | `app/history/page.tsx` |
| Health / stats | `routers/system.py` | `components/Sidebar.tsx` status |

## Explicitly NOT implemented (do not claim otherwise)

- CLIP / any vision model inference (belongs to Image Search phase)
- Image search, VLM, BLIP, uploads
- Non-English translation batch (abstraction ready, batch not run)
- Cloud APIs (Gemini/Grok/Clerk) in the runtime path
- Keyword/full-text search in the retrieval path
