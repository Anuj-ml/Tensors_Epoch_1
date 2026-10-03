ReelMind AI B-Roll Studio — Features

1. Product Summary

ReelMind is an AI-powered B-roll discovery and editing studio. It retrieves video scenes by semantic meaning instead of relying only on filenames, tags, or manually added keywords.

Core workflow:

UNDERSTAND → DISCOVER → EXPLORE → SELECT → EDIT

Core product promise:

Search for the meaning of the video you need, not only the words used to describe it.

2. Product Goals

Primary goals

Find useful B-roll from natural-language descriptions.

Return exact moments instead of forcing users to inspect long videos.

Support text-to-video and image-to-video semantic retrieval.

Help users discover alternatives without rewriting queries.

Preserve discovery context across searches and projects.

Turn selected clips into a rough visual sequence.

Non-goals for initial release

Full professional NLE replacement.

Advanced color grading.

Complex audio mixing.

Automatic final-cut production for publish-ready video.

Video generation as a core retrieval feature.

3. Feature: AI Brain

Purpose

AI Brain is ReelMind's primary search and understanding surface. It accepts natural-language requests and converts them into structured semantic concepts before retrieval.

Supported input

Natural-language query.

Multi-sentence visual description.

Script.

Optional search filters.

Semantic query understanding

Example:

Query:
"A tired programmer working late at night"

Person: 1 person
Actions: typing, working, looking at screen
Objects: laptop, coffee, desk
Environment: dark office, indoor
Context: late-night work
Mood: focused, tired

Searchable concepts

People and count.

Actions.

Objects.

Environment.

Context.

Mood.

Weather.

Shot type.

Duration.

Orientation.

Semantic retrieval

Each indexed scene can contain:

Visual embedding.

Text or semantic embedding.

Scene description.

Detected objects.

Detected actions when available.

Environment labels.

Timestamp range.

Source video metadata.

Optional transcript/audio metadata.

Query representation is compared with indexed scene representations to rank relevant candidates.

Result card

Each result should expose:

Preview image/video.

Source video.

Start and end timestamp.

Scene description.

Relevance score.

Detected concepts.

Why the result matches.

Add to Story.

Find Similar.

Open in Editor.

Exact moment retrieval

Long videos must be searchable at scene/shot level.

Example:

Source: office_day.mp4
Scene: 00:14–00:22
Reason: Person typing on laptop in a dark office.

What AI Sees

Optional inspection mode. Show evidence used to describe a scene:

People.

Objects.

Actions.

Environment.

Context.

Bounding boxes when the detector provides them.

This feature improves transparency and helps users understand why a scene was retrieved.

Smart search controls

Search refinements should live inside AI Brain rather than separate database-style pages.

Filters:

Mood.

Environment.

Action.

Object.

Shot type.

Number of people.

Similarity threshold.

Duration.

Orientation.

Find Similar

A selected result can become a new retrieval query. Support two modes:

Visual similarity.

Semantic similarity.

The result should preserve useful constraints from the selected scene where possible.

Script-to-B-roll

AI Brain accepts a script and divides it into visual beats.

Example:

Script:
"Every night, thousands of developers work long after everyone else has gone home."

Visual beats:
1. Developers working
2. Night environment
3. Computer/laptop
4. Empty office
5. Person working alone

Each beat becomes an independent search task. Results stay grouped by beat until the user selects clips.

4. Feature: Image Search

Purpose

Image Search lets users provide a reference image, screenshot, or frame and retrieve semantically related video scenes.

Flow

Reference image
      ↓
Visual understanding
      ↓
Semantic representation
      ↓
Video retrieval
      ↓
Related video scenes

Understand from image

Objects.

People.

Environment.

Composition.

Scene type.

Visual context.

Retrieval behavior

Image Search should not depend only on pixel similarity. It should search for visual concepts represented in the image.

Example:

Reference:
Person driving through rainy city at night.

Possible concepts:
Driving · car · rain · night · city · road · urban

Result actions

Preview.

Add to Story.

Add to Clip Editor.

Find Similar.

Save to History.

5. Feature: Explore

Purpose

Explore exposes the semantic structure of the indexed library as an interactive visual map.

Each point represents a searchable scene. Nearby points represent scenes with related semantic or multimodal embeddings.

Core behavior

Zoom.

Pan.

Hover for scene preview.

Click to inspect scene.

Search for a concept.

Highlight matching regions.

Open scene in Editor.

Add scene to Story.

Typical discovery path

City → Night → Rain → Driving → Cars

Initial visualization

Use a 2D embedding projection for the first release. UMAP or another dimensionality-reduction method can generate a stable map offline during indexing.

The UI must treat the map as a navigation surface, not decoration.

Performance rule

Do not render every searchable segment as a full DOM node. Use a canvas/WebGL-based point layer when scene count becomes large.

6. Feature: History

Purpose

History preserves discovery context so users can return to previous searches and selected scenes.

Search history item

Store:

Query text.

Search type.

Result count.

Created timestamp.

Selected result IDs when available.

Project ID when available.

Saved searches

Users can save reusable searches such as:

Night City B-Roll.

Office Work.

Technology Closeups.

Travel Transitions.

Project context

History can reference:

Selected clips.

Image searches.

Search queries.

Recently opened scenes.

Clips sent to Editor.

History should be append-first for discovery events. Avoid storing large media payloads in the history table.

7. Feature: Clip Editor

Purpose

Clip Editor turns selected B-roll into a rough sequence.

Inputs

Clips from:

AI Brain.

Image Search.

Explore.

History.

Initial editor capabilities

Add clip.

Remove clip.

Reorder clip.

Trim clip.

Preview sequence.

Select clip ranges.

Save storyboard.

Generate rough cut.

Editor views

Preview.

Selected clips/storyboard.

Timeline.

Storyboard

Example:

[01] → [02] → [03] → [04]
Person   Laptop   Clock   Office
Working  Close-up Night    Empty

Storyboard should be reorderable before timeline generation.

Generate Rough Cut

The main AI editor action creates a preliminary sequence from selected clips.

Workflow:

Selected clips
↓
Evaluate visual relevance
↓
Select strongest moments
↓
Trim clips
↓
Order clips
↓
Match narrative beats
↓
Generate preview

The output remains editable. It is not treated as a final rendered film.

Script-aware sequencing

When selections originate from Script-to-B-roll, retain beat IDs. Rough Cut can use those IDs to preserve narrative order.

Example:

Beat 1: Person typing
Beat 2: Laptop close-up
Beat 3: Clock at night
Beat 4: Empty office

8. Cross-Feature Workflow

                  ┌──────────────┐
                  │   AI BRAIN   │
                  │ Text / Script│
                  └──────┬───────┘
                         │
            ┌────────────┴────────────┐
            │                         │
     ┌──────▼───────┐         ┌──────▼───────┐
     │ IMAGE SEARCH │         │    EXPLORE    │
     │ Image → Video│         │  Visual Map   │
     └──────┬───────┘         └──────┬────────┘
            │                        │
            └──────────┬─────────────┘
                       ▼
                ┌──────────────┐
                │    HISTORY   │
                │ Save / Return│
                └──────┬───────┘
                       ▼
                ┌──────────────┐
                │ CLIP EDITOR  │
                │  Build Story │
                └──────┬───────┘
                       ▼
                ┌──────────────┐
                │  ROUGH CUT   │
                └──────────────┘

The architecture should keep these flows connected through shared scene IDs, project IDs, search IDs, and clip references.

9. End-to-End User Journey

1. Describe

User enters:

A person walking alone through a rainy city at night.

2. Understand

AI Brain extracts person, walking, city, street, alone, night, rain, and cinematic/isolated context.

3. Discover

System retrieves timestamped scenes.

4. Explore

User opens Explore and navigates related scenes around city, night, rain, and walking.

5. Select

User adds useful scenes to Story.

6. Build

Selected scenes appear in Clip Editor.

7. Generate

User runs Generate Rough Cut.

8. Refine

User previews, reorders, and trims the generated sequence.

10. Feature Acceptance Criteria

AI Brain

Natural-language query returns semantically relevant scene candidates.

Search results show timestamps.

Search can return a short relevant segment from a long source video.

User can inspect AI understanding for a selected scene.

Find Similar returns related scenes.

Script input produces grouped visual beats.

Image Search

User can upload an image.

Image is converted into searchable visual concepts or an embedding.

Related scenes return with timestamps.

Results can enter Story or Editor.

Explore

Scene embeddings render as navigable points.

Selecting a point opens scene metadata and preview.

Search can highlight relevant points.

Users can move from Explore directly to Editor.

History

Searches persist.

Previous searches can be reopened.

Saved searches can be deleted.

Project context remains attached to relevant discovery actions.

Clip Editor

Clips can be added, removed, reordered, and trimmed.

Storyboard order maps to timeline order.

Generate Rough Cut produces an editable sequence.

User can save work and reopen it.

11. Future Extensions

Keep these outside core MVP until retrieval and editing are reliable:

Audio-aware search.

Speech/transcript search.

Automatic music matching.

Video generation.

Automatic captions.

Brand/style presets.

Collaborative projects.

Professional timeline export.

Premiere Pro integration.

DaVinci Resolve integration.

Personalized ranking based on creator feedback.