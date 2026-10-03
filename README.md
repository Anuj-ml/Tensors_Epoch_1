# fRAMES Studio  Architecture

## 1. Architecture Goal

Build ReelMind as a semantic video retrieval system with a lightweight editing layer.

Primary requirement:

> Convert text or image intent into searchable scene representations, retrieve exact timestamped moments, and move selected scenes into an editable rough-cut workflow.

Design principle:

**Index once. Retrieve many times. Keep media separate from metadata and embeddings.**

---

# 2. Reference Stack

This is a practical default stack. Replace components only when the existing codebase already provides equivalent capability.

| Layer | Reference choice | Responsibility |
|---|---|---|
| Web app | Next.js + TypeScript | UI, routing, server actions/API proxy |
| UI | Tailwind + existing component system | Search, Explore, History, Editor |
| API | FastAPI + Python | Search, ingestion, media metadata, AI orchestration |
| Video processing | FFmpeg | Probe, decode, scene clips, thumbnails, transcode |
| Vision embeddings | CLIP/SigLIP-class model | Image/video semantic representation |
| Text embeddings | Same compatible embedding space where possible | Text-to-scene retrieval |
| Vector DB | Qdrant | Scene vector search + payload filters |
| Relational DB | PostgreSQL | Users, projects, searches, scenes, clips, history |
| Object storage | S3-compatible storage | Source videos, thumbnails, generated previews |
| Cache/queue | Redis only when needed | Job state, caching, background jobs |
| Background workers | Python worker | Video indexing, embedding, transcription, rough-cut jobs |

Do not add every component on day one. For a local MVP, PostgreSQL + Qdrant + filesystem/object storage can cover core behavior.

---

# 3. System Context

```text
                         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                         â”‚   Next.js Web App   â”‚
                         â”‚                     â”‚
                         â”‚ AI Brain            â”‚
                         â”‚ Image Search        â”‚
                         â”‚ Explore             â”‚
                         â”‚ History             â”‚
                         â”‚ Clip Editor         â”‚
                         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                    â”‚ HTTPS
                                    â–¼
                         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                         â”‚      FastAPI        â”‚
                         â”‚     API Layer       â”‚
                         â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜
                                 â”‚     â”‚
                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                    â–¼                                 â–¼
          â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
          â”‚ Search / AI     â”‚                â”‚ Project / CRUD  â”‚
          â”‚ Orchestrator    â”‚                â”‚ Services        â”‚
          â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜                â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                   â”‚                                  â”‚
          â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”                 â”Œâ”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”
          â–¼                 â–¼                 â–¼                â–¼
     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
     â”‚ Qdrant  â”‚      â”‚ AI Models  â”‚   â”‚ PostgreSQL â”‚   â”‚ Object Storeâ”‚
     â”‚ Vectors â”‚      â”‚ Vision/Textâ”‚   â”‚ Metadata   â”‚   â”‚ Media       â”‚
     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜      â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                              â–²
                              â”‚
                       â”Œâ”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”
                       â”‚ Worker Queue â”‚
                       â”‚ / Workers    â”‚
                       â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜
                              â”‚
                       â”Œâ”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”
                       â”‚ FFmpeg +      â”‚
                       â”‚ Scene Indexer â”‚
                       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

# 4. Core Data Model

## Video

Represents one source asset.

```text
video
- id
- project_id nullable
- storage_key
- filename
- duration_ms
- width
- height
- fps
- orientation
- created_at
```

## Scene

Represents a searchable timestamped segment.

```text
scene
- id
- video_id
- start_ms
- end_ms
- shot_index
- thumbnail_key
- description
- environment
- mood
- people_count
- orientation
- created_at
```

## Detection

Optional structured visual evidence.

```text
detection
- id
- scene_id
- type
- label
- confidence
- bbox_x
- bbox_y
- bbox_w
- bbox_h
```

## Embedding

Vector data belongs in Qdrant. PostgreSQL can store model/version metadata.

```text
embedding_meta
- scene_id
- model_name
- model_version
- modality
- vector_version
```

## Search

```text
search
- id
- user_id
- project_id nullable
- type text|image|script|similar
- query_text nullable
- source_scene_id nullable
- created_at
```

## Search result

Persist IDs and ranking metadata, not duplicate scene records.

```text
search_result
- search_id
- scene_id
- rank
- score
- explanation nullable
```

## Project

```text
project
- id
- user_id
- name
- created_at
- updated_at
```

## Story clip

A reference to a scene plus user-specific trim/order data.

```text
story_clip
- id
- project_id
- scene_id
- order_index
- source_start_ms
- source_end_ms
- in_ms
- out_ms
- beat_id nullable
```

Do not duplicate source media for every Story clip.

---

# 5. Video Ingestion Pipeline

```text
Upload video
    â”‚
    â–¼
Probe media with FFmpeg
    â”‚
    â–¼
Scene/shot segmentation
    â”‚
    â–¼
Extract representative frames
    â”‚
    â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â–¼               â–¼
Visual embedding   Visual metadata
    â”‚               â”‚
    â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜
            â–¼
Optional transcript/audio analysis
            â”‚
            â–¼
Write scene metadata to PostgreSQL
            â”‚
            â–¼
Write vectors + filter payload to Qdrant
            â”‚
            â–¼
Generate thumbnails/previews
            â”‚
            â–¼
Scene searchable
```

### Segmentation strategy

Start with shot/scene boundaries. Avoid frame-level indexing in MVP because vector count and query cost grow rapidly.

For each scene, retain:

- Start/end timestamp.
- One or more representative frames.
- One visual embedding or pooled representation.
- Semantic metadata.

Add finer temporal windows only when evaluation shows shot-level retrieval is too coarse.

---

# 6. Semantic Retrieval Pipeline

## Text search

```text
User query
   â”‚
   â–¼
Query understanding
   â”‚
   â”œâ”€â”€ concepts
   â”œâ”€â”€ optional filters
   â””â”€â”€ narrative intent
   â”‚
   â–¼
Text embedding
   â”‚
   â–¼
Vector search in Qdrant
   â”‚
   â–¼
Metadata filters
   â”‚
   â–¼
Candidate scenes
   â”‚
   â–¼
Optional reranking
   â”‚
   â–¼
Top results
```

## Image search

```text
Reference image
   â”‚
   â–¼
Image embedding / visual understanding
   â”‚
   â–¼
Vector search
   â”‚
   â–¼
Optional metadata filtering
   â”‚
   â–¼
Top scene candidates
```

## Find Similar

Use the selected scene embedding as the query vector.

Support:

- Pure visual nearest-neighbor retrieval.
- Semantic retrieval using scene description embedding.
- Hybrid search when both vectors exist.

---

# 7. Retrieval Ranking

Do not expose raw vector distance as the only relevance score.

Reference ranking pipeline:

```text
Vector similarity
      +
Query concept overlap
      +
Metadata/filter match
      +
Optional reranker score
      =
Final ranking score
```

Keep score components available internally for debugging.

User-facing result card can show a normalized relevance indicator and explanation. Exact numeric score is optional.

---

# 8. Query Understanding Contract

Use a structured internal representation.

Example:

```json
{
  "query": "A tired programmer working late at night",
  "entities": ["person", "laptop", "coffee"],
  "actions": ["typing", "working"],
  "environment": ["office", "indoor", "dark"],
  "context": ["late-night work"],
  "mood": ["focused", "tired"],
  "filters": {
    "people_count": 1
  }
}
```

Treat this structure as an internal contract. UI labels may change without changing retrieval code.

---

# 9. Explore Architecture

Explore should use the same indexed scene identity as search.

Offline/index-time:

```text
Scene embeddings
      â”‚
      â–¼
Dimensionality reduction
      â”‚
      â–¼
2D coordinates
      â”‚
      â–¼
Store projection version
```

Runtime:

```text
Map viewport
   â”‚
   â–¼
Fetch points for visible region
   â”‚
   â–¼
Render point layer
   â”‚
   â–¼
Hover/click scene ID
   â”‚
   â–¼
Load scene metadata + preview
```

Do not calculate UMAP/t-SNE in the browser for the full corpus.

For larger libraries, precompute the map and tile or cluster points.

---

# 10. History Architecture

History is event metadata, not a media store.

Recommended events:

```text
search.created
search.reopened
scene.opened
scene.selected
scene.added_to_story
scene.find_similar
image_search.created
rough_cut.generated
```

Each event references IDs. Large payloads stay out of the history table.

Saved searches can be represented as named search definitions rather than duplicated result sets.

---

# 11. Clip Editor Architecture

The editor stores references to source scenes and user trim/order decisions.

```text
Scene
  â”‚
  â””â”€â”€ StoryClip
        â”œâ”€â”€ source range
        â”œâ”€â”€ trim range
        â”œâ”€â”€ order
        â””â”€â”€ script beat
```

### Preview

For MVP, compose previews from source clip URLs or generated short proxies.

### Rough Cut

Rough Cut job receives:

```json
{
  "project_id": "...",
  "clip_ids": ["..."],
  "beat_order": ["beat-1", "beat-2"],
  "options": {
    "max_duration_ms": 30000
  }
}
```

Worker returns a sequence manifest first. Rendering can remain separate.

Example sequence manifest:

```json
{
  "clips": [
    {"scene_id": "s1", "in_ms": 1400, "out_ms": 4200},
    {"scene_id": "s9", "in_ms": 0, "out_ms": 3100}
  ]
}
```

This separation keeps editing state fast and reversible.

---

# 12. API Surface

Reference endpoints:

```text
POST   /api/videos
POST   /api/videos/{video_id}/index
GET    /api/videos/{video_id}
GET    /api/scenes/{scene_id}
GET    /api/scenes/{scene_id}/preview

POST   /api/search/text
POST   /api/search/image
POST   /api/search/script
POST   /api/search/similar
GET    /api/search/{search_id}

GET    /api/explore/points
GET    /api/explore/scene/{scene_id}

GET    /api/history
POST   /api/history/saved
DELETE /api/history/saved/{id}

GET    /api/projects/{project_id}
POST   /api/projects/{project_id}/clips
PATCH  /api/projects/{project_id}/clips/{clip_id}
DELETE /api/projects/{project_id}/clips/{clip_id}
POST   /api/projects/{project_id}/rough-cut
GET    /api/projects/{project_id}/rough-cut/{job_id}
```

Exact route style can follow the existing application convention.

---

# 13. Background Jobs

Long-running work should not block normal API requests.

Jobs:

- Video indexing.
- Scene extraction.
- Embedding generation.
- Transcript extraction.
- Thumbnail generation.
- Explore projection generation.
- Rough-cut generation.
- Final preview rendering when added.

Job states:

```text
queued â†’ running â†’ succeeded
                 â””â†’ failed
```

Every job needs an ID and error state. Do not hide worker failures behind permanent loading states.

---

# 14. Storage Rules

## Object storage

Store:

- Original video.
- Scene thumbnails.
- Proxy video.
- Generated preview.
- Optional rendered rough cut.

## PostgreSQL

Store:

- Product metadata.
- Relationships.
- User/project state.
- Search metadata.
- Editor state.
- Job state.

## Qdrant

Store:

- Scene vectors.
- Image/text vector representations where applicable.
- Lightweight filter payload.
- Scene ID and video ID.

Do not put large descriptions or media blobs into vector payloads.

---

# 15. Security and Reliability

- Validate upload MIME type and file extension.
- Enforce maximum upload size and duration.
- Never trust client-provided timestamps or media metadata.
- Store generated files under controlled keys.
- Prevent path traversal in source filenames.
- Restrict project access by authenticated user/project ownership.
- Do not expose object-store credentials to browser clients.
- Use signed URLs for private media.
- Record model/version used for embeddings.
- Make ingestion jobs idempotent.
- Make retries safe.
- Keep source media immutable.

---

# 16. Observability

Track retrieval quality, not only API latency.

Core metrics:

- Search latency.
- Indexing latency.
- Queue latency.
- Search result count.
- Click-through rate on results.
- Add-to-Story rate.
- Find-Similar usage.
- Rough-cut generation time.
- Retrieval recall on evaluation set.
- Top-k relevance.

Log:

- Search ID.
- Model/version.
- Query type.
- Candidate count.
- Top-k scores.
- Filters used.
- Reranker version.

Do not log raw private media or sensitive user content unnecessarily.

---

# 17. Evaluation Strategy

Build a small labeled benchmark before tuning ranking.

Each benchmark case contains:

```text
query
expected scene IDs
acceptable scene IDs
negative scene IDs
```

Measure:

- Recall@K.
- Precision@K.
- MRR.
- NDCG where useful.
- Median search latency.

For image search, store reference image plus expected scene IDs.

For Script-to-B-roll, evaluate beat-level retrieval separately from final sequence ordering.

---

# 18. MVP Deployment

Local development:

```text
Next.js
FastAPI
PostgreSQL
Qdrant
FFmpeg
Local object storage or filesystem
Worker process
```

Production can move object storage and workers to managed infrastructure without changing the core data model.

Avoid Kubernetes for initial deployment unless required by existing infrastructure.

---

# 19. Architecture Decisions

### Decision: Scene-level indexing first

Reason: Lower vector count, faster indexing, simpler timestamps. Add finer temporal retrieval only after benchmark evidence.

### Decision: Separate vector store from relational DB

Reason: Vector search and transactional application state have different access patterns.

### Decision: Store editor state as references

Reason: Reuse source media. Keep edits small, reversible, and fast.

### Decision: Treat AI explanations as derived metadata

Reason: Explanations can change with model versions. Source scene identity remains stable.

### Decision: Keep model/version metadata

Reason: Re-indexing should be reproducible and comparable.
