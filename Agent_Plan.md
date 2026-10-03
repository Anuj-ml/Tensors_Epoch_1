ReelMind AI B-Roll Studio — Agent Plan

1. Objective

Build ReelMind in small, testable slices. Every agent owns one bounded outcome, uses existing code before adding dependencies, and leaves the repository runnable.

Core implementation order:

Foundation → Ingestion → Retrieval → Discovery → History → Editor → Rough Cut → Evaluation

Do not build all five product surfaces in parallel before the retrieval pipeline works.

2. Agent Rules

Inspect repository before changing files.

Reuse existing stack and components.

Add no dependency without a concrete requirement.

Keep interfaces explicit between frontend, API, worker, DB, and vector store.

Fix root cause, not caller symptoms.

Make long-running jobs resumable and retry-safe.

Keep source videos immutable.

Do not introduce speculative abstractions.

Add focused tests with each non-trivial backend change.

Run relevant proof after each agent task.

Stop when task acceptance criteria pass.

3. Shared Contracts

Agents must use these stable IDs:

user_id
project_id
video_id
scene_id
search_id
story_clip_id
job_id
beat_id

Core backend contracts:

Scene
SearchRequest
SearchResult
ScriptBeat
StoryClip
RoughCutJob

Do not let frontend-specific component shapes become the canonical backend contract.

4. Phase 0 — Repository Recon Agent

Goal

Understand existing repository structure before implementing ReelMind.

Tasks

Identify frontend framework.

Identify backend framework.

Identify current DB.

Identify existing storage layer.

Identify existing AI/embedding code.

Identify existing video utilities.

Identify reusable UI components.

Find current auth/project model.

Find current API conventions.

Find test and lint commands.

Output

Create or update:

AGENT_RECON.md

Include:

Existing architecture.

Reusable modules.

Missing infrastructure.

Files to modify.

Dependencies already installed.

Risks.

Stop condition

No coding until repository boundaries are clear.

5. Phase 1 — Foundation Agent

Goal

Create minimal data and service foundations.

Tasks

Add project/video/scene/search/story models as needed.

Add migrations.

Add Qdrant integration only if not already present.

Add object-storage abstraction only if needed by current app.

Add job model/state.

Add shared IDs and validation.

Add health checks for required services.

Acceptance

Database migration runs from clean state.

API starts.

Vector DB connectivity is verified.

Existing application behavior stays intact.

Do not build

Explore UI.

Full editor.

Complex agent framework.

6. Phase 2 — Video Ingestion Agent

Goal

Turn source videos into searchable scene records.

Tasks

Upload/store source video.

Probe video with FFmpeg.

Detect scene/shot boundaries.

Create scene rows.

Extract representative frames.

Generate thumbnails.

Generate visual embeddings.

Store vectors in Qdrant.

Store metadata in PostgreSQL.

Track job state.

Idempotency

Re-running indexing for the same video and model version must not create duplicate scenes.

Use a deterministic indexing key such as:

video_id + segmentation_version + embedding_model_version

Acceptance

Given one test video:

Scenes are created.

Timestamps are valid.

Thumbnails open.

Vectors exist.

Scene IDs map back to the source video.

Indexing can resume after a worker failure.

7. Phase 3 — AI Brain Agent

Goal

Implement semantic text retrieval.

Tasks

Parse natural-language query.

Generate query embedding.

Search Qdrant.

Apply metadata filters.

Return ranked scene IDs.

Load scene metadata from PostgreSQL.

Produce result explanations from stored metadata.

Return exact timestamps.

Result contract

{
  "search_id": "...",
  "results": [
    {
      "scene_id": "...",
      "video_id": "...",
      "start_ms": 14000,
      "end_ms": 22000,
      "score": 0.91,
      "description": "Person typing on laptop in dark office",
      "why_match": ["laptop", "typing", "dark office", "night work"]
    }
  ]
}

Acceptance

Text queries return relevant timestamped scenes from the indexed test set.

8. Phase 4 — AI Brain UI Agent

Goal

Build search UI on top of the working retrieval API.

Tasks

Search input.

Loading state.

Result cards.

Timestamp display.

Preview.

Add to Story.

Find Similar.

What AI Sees drawer/panel.

Filter controls.

UX rule

Do not hide search state. Users need clear status for querying, loading previews, and indexing-dependent results.

Acceptance

A user can search, inspect a result, jump to its exact moment, and send it to Story without leaving AI Brain.

9. Phase 5 — Image Search Agent

Goal

Retrieve video scenes from a reference image.

Tasks

Image upload/drop zone.

Validate image type and size.

Generate image embedding.

Query compatible scene vectors.

Return timestamped results.

Reuse result card component.

Add Find Similar and Add to Story actions.

Acceptance

A reference image retrieves visually related test scenes without requiring manual text tagging.

10. Phase 6 — Find Similar Agent

Goal

Make result discovery recursive.

Tasks

Accept scene ID.

Load scene vector.

Search nearest neighbors.

Exclude source scene.

Preserve project/history context.

Reuse result presentation.

Acceptance

Clicking Find Similar from any result opens a new search with related scenes.

11. Phase 7 — Explore Agent

Goal

Build visual semantic navigation.

Tasks

Generate 2D scene coordinates offline.

Store projection version.

Create point-map UI.

Add hover preview.

Add click-to-open scene.

Add search highlighting.

Add filters if already supported by search API.

Performance rule

Start with a bounded dataset. Introduce viewport-based point loading or clustering only when actual scene count requires it.

Acceptance

User can move from semantic map point to scene preview and then to Story or Editor.

12. Phase 8 — Script-to-B-roll Agent

Goal

Convert a script into grouped visual retrieval tasks.

Tasks

Accept script text.

Segment into visual beats.

Assign beat IDs.

Run retrieval per beat.

Group results by beat.

Allow selection across beats.

Acceptance

Given a short script, UI displays ordered visual beats with independently searchable scene candidates.

Important constraint

Do not generate a complete video automatically. This phase only structures search and selection.

13. Phase 9 — History Agent

Goal

Persist discovery context.

Tasks

Record search creation.

Record search reopen.

Record scene open.

Record scene selection.

Record Add to Story.

Record Find Similar.

Add search-history page/panel.

Add saved searches.

Acceptance

User can close and reopen ReelMind and restore meaningful discovery history without storing media blobs in history.

14. Phase 10 — Story + Clip Editor Agent

Goal

Create minimal editable sequence from selected clips.

Tasks

Story container per project.

Add/remove clips.

Drag reorder.

Clip trim.

Storyboard view.

Timeline view.

Preview selected sequence.

Persist editor state.

Acceptance

User can select clips from AI Brain/Image Search/Explore/History and edit one consistent Story.

15. Phase 11 — Rough Cut Agent

Goal

Generate an editable sequence from selected Story clips.

Tasks

Define rough-cut job request.

Rank candidate clip moments.

Use script beat IDs where present.

Trim weak portions.

Generate ordered sequence manifest.

Return job progress.

Show preview.

Initial algorithm

Keep first version deterministic and simple:

For each beat:
  rank selected clips by retrieval score
  choose strongest non-duplicate clip
  apply user trim when present
Preserve beat order
Return sequence manifest

Do not build a complex autonomous editing agent before evaluation data exists.

Acceptance

Generate Rough Cut creates a valid, editable sequence with stable ordering and no source media mutation.

16. Phase 12 — Evaluation Agent

Goal

Measure retrieval quality before adding more AI complexity.

Tasks

Build a small benchmark containing:

Natural-language queries.

Expected scenes.

Negative scenes.

Image references.

Script beats.

Measure:

Recall@K.

Precision@K.

MRR.

NDCG when useful.

Median search latency.

Acceptance

Every retrieval change can be compared against a stable benchmark.

17. Phase 13 — QA Agent

Goal

Check complete workflow and edge cases.

Test matrix

Upload

Unsupported file.

Oversized file.

Corrupt video.

Zero-duration media.

Duplicate upload.

Search

Empty query.

Very long query.

No result.

Many results.

Filter combination.

Similarity search on missing scene.

Image

Unsupported image.

Large image.

Blank image.

Image search with no results.

Editor

Delete last clip.

Reorder many clips.

Invalid trim range.

Save/reload project.

Worker failure during rough cut.

Security

User cannot access another project.

Signed media URLs expire.

Uploaded filename cannot escape storage path.

API rejects invalid IDs and payloads.

18. Agent Dependency Graph

Recon
  │
  ▼
Foundation
  │
  ▼
Ingestion
  │
  ▼
AI Brain API
  │
  ├───────────────┐
  ▼               ▼
AI Brain UI    Image Search
  │               │
  └───────┬───────┘
          ▼
      Find Similar
          │
          ▼
       Explore
          │
          ▼
   Script-to-B-roll
          │
          ▼
        History
          │
          ▼
   Story / Clip Editor
          │
          ▼
       Rough Cut
          │
          ▼
      Evaluation
          │
          ▼
          QA

Some frontend work can start after API contracts exist. Do not merge partial UI that has no working backend path unless it is explicitly a static prototype task.

19. Agent Handoff Format

Every agent should end with:

## Changed
- file/path
- file/path

## Behavior
- what now works

## Proof
- command run
- test result

## Known limitations
- one line per limitation

## Next agent
- exact next bounded task

Keep handoffs factual. Do not describe unverified behavior as complete.

20. Merge Gates

Merge phase only when:

Existing tests remain green.

New focused tests pass.

API contracts are documented.

No known data-loss path exists.

Long-running jobs expose failure state.

No duplicate media or vector records are created on retry.

Frontend does not assume backend fields that do not exist.

21. Minimal Agent Prompts

Recon Agent

Inspect repository only. Map existing frontend, backend, DB, storage, AI, video, auth, test, and deployment code. Identify reusable components and missing pieces for ReelMind. Do not modify application code. Write concise AGENT_RECON.md with evidence-backed findings and exact files to reuse or change.

Ingestion Agent

Implement minimal idempotent video indexing. Probe video, segment scenes, create scene metadata, extract representative frames, generate embeddings, write vectors to Qdrant, and persist metadata in PostgreSQL. Reuse existing code. Add focused tests. Verify retry safety. Stop when acceptance criteria pass.

Retrieval Agent

Implement semantic text retrieval over indexed scenes. Accept natural-language query, generate query representation, search Qdrant, apply supported metadata filters, load scene metadata, rank results, and return exact timestamps plus match explanation. Add focused tests against fixed fixtures. Do not add speculative abstractions.

Frontend Search Agent

Build AI Brain UI over existing search API. Reuse existing components and design system. Add query input, loading/error/empty states, result cards, timestamps, preview, Add to Story, Find Similar, filters, and What AI Sees. Keep API assumptions explicit. Verify end-to-end interaction.

Explore Agent

Build Explore over indexed scene embeddings. Use precomputed 2D coordinates. Support pan/zoom, scene hover, scene click, search highlighting, and Add to Story. Do not calculate full-corpus projection in the browser. Optimize only after measuring scene count.

Editor Agent

Build minimal Story and Clip Editor using scene references. Support add, remove, reorder, trim, storyboard, timeline, preview, and persistence. Keep source media immutable. Add focused tests for ordering and trim boundaries.

Rough Cut Agent

Implement deterministic first-pass rough cut from selected Story clips. Preserve script beat order when beat IDs exist. Rank within each beat, choose strongest non-duplicate clip, apply valid trims, and return editable sequence manifest. Expose job progress and failure. Do not build autonomous editing beyond this scope.

Evaluation Agent

Create fixed retrieval benchmark for text, image, and script-beat search. Measure Recall@K, Precision@K, MRR, and latency. Record baseline. Do not change ranking code unless explicitly requested.

22. Stop Conditions

Stop implementation and surface evidence when:

Retrieval quality is worse than baseline.

Indexing creates duplicates.

Job retry changes scene IDs unexpectedly.

Editor modifies source media.

Cross-user project access is possible.

A required model or external service is unavailable.

A new dependency is required but not justified.

Do not hide these issues with fallback logic that changes product semantics.

23. MVP Definition

ReelMind MVP is complete when one user can:

Upload video
   ↓
Index scenes
   ↓
Search with natural language
   ↓
Retrieve exact moments
   ↓
Inspect AI understanding
   ↓
Find similar scenes
   ↓
Add clips to Story
   ↓
Reorder and trim clips
   ↓
Generate rough cut
   ↓
Save and reopen project

Image Search and Explore can ship in the same MVP only when they use the same scene index and do not destabilize core retrieval.

24. Post-MVP Queue

Defer until benchmark and core workflow are stable:

Audio-aware retrieval.

Transcript-heavy retrieval.

Music matching.

Personalized ranking.

Collaboration.

Premiere Pro export.

DaVinci Resolve export.

Advanced transitions.

Automatic captions.

Video generation.