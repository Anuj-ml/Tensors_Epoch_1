"use client";

export const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8010";

export type SearchResult = {
  segment_id: string;
  video_id: string;
  start_ms: number;
  end_ms: number;
  start_clock: string;
  end_clock: string;
  duration_ms: number | null;
  score: number;
  description: string;
  description_original?: string | null;
  why_match: string[];
  concepts: string[];
  matched_annotations: {
    annotation_id: string;
    description: string;
    language: string | null;
    score: number;
  }[];
  video_filename: string;
  thumbnail_url: string | null;
  preview_endpoint: string;
  width?: number | null;
  height?: number | null;
};

export type SearchResponse = {
  search_id: string;
  query: string;
  query_type: string;
  understanding: { concepts: string[]; word_count: number; is_question: boolean };
  results: SearchResult[];
  result_count: number;
  latency_ms?: number;
  score_note: string;
};

export type StoryClip = {
  story_clip_id: string;
  segment_id: string;
  video_id: string;
  source_start_ms: number;
  source_end_ms: number;
  source_start_clock: string;
  source_end_clock: string;
  duration_ms: number;
  timeline_start_ms: number;
  timeline_end_ms: number;
  order_index: number;
  description: string | null;
  video_filename: string;
  thumbnail_url: string | null;
  preview_endpoint: string;
};

export type StoryProject = {
  project_id: string;
  name: string;
  created_at?: string;
  updated_at?: string;
  clips: StoryClip[];
  total_duration_ms: number;
};

export type Job = {
  job_id: string;
  job_type: string;
  status: string;
  progress: number;
  error: string | null;
  detail: Record<string, unknown> | null;
};

export type SearchHistoryItem = {
  search_id: string;
  query: string;
  query_type: string;
  result_count: number;
  is_saved: boolean;
  saved_label: string | null;
  created_at: string;
};

export type ExplorePoint = {
  segment_id: string;
  x: number;
  y: number;
  video_id: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number | null;
  description: string | null;
  video_filename: string;
  thumbnail_url: string | null;
  preview_endpoint: string;
};

export type Beat = {
  beat_id: string;
  beat_index: number;
  text: string;
  visual_intent: string;
  selected_segment_id: string | null;
  candidates: SearchResult[];
};

export type Analysis = {
  analysis_id: string;
  script: string;
  status: string;
  beat_count: number;
  beats: Beat[];
};

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function postSearch(
  query: string,
  topK = 10,
  filters?: Record<string, unknown>,
): Promise<SearchResponse> {
  const res = await fetch(`${API}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: topK, filters: filters || null }),
  });
  return j<SearchResponse>(res);
}

export async function findSimilar(segmentId: string, topK = 8): Promise<SearchResponse> {
  const res = await fetch(`${API}/api/search/find-similar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: segmentId, top_k: topK }),
  });
  return j<SearchResponse>(res);
}

export async function previewUrl(segmentId: string): Promise<string> {
  const res = await fetch(
    `${API}/api/segments/${encodeURIComponent(segmentId)}/preview`,
    { method: "POST" },
  );
  const body = await j<{ url: string }>(res);
  return `${API}${body.url}`;
}

export function thumbUrl(thumbnailUrl: string | null): string | null {
  return thumbnailUrl ? `${API}${thumbnailUrl}` : null;
}

export async function listProjects() {
  const res = await fetch(`${API}/api/story/projects`);
  return j<{ projects: { project_id: string; name: string; clip_count: number }[] }>(res);
}

export async function createProject(name: string) {
  const res = await fetch(`${API}/api/story/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return j<{ project_id: string; name: string }>(res);
}

export async function getProject(id: string): Promise<StoryProject> {
  const res = await fetch(`${API}/api/story/projects/${id}`);
  return j<StoryProject>(res);
}

export async function addClip(projectId: string, segmentId: string) {
  const res = await fetch(`${API}/api/story/projects/${projectId}/clips`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: segmentId }),
  });
  return j<StoryClip>(res);
}

export async function updateClip(clipId: string, patch: Record<string, unknown>) {
  const res = await fetch(`${API}/api/story/clips/${clipId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return j<StoryClip>(res);
}

export async function deleteClip(clipId: string) {
  const res = await fetch(`${API}/api/story/clips/${clipId}`, { method: "DELETE" });
  return j<{ deleted: string }>(res);
}

export async function reorderClips(projectId: string, clipIds: string[]) {
  const res = await fetch(`${API}/api/story/projects/${projectId}/order`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clip_ids: clipIds }),
  });
  return j<{ order: string[] }>(res);
}

export async function generateRoughCut(projectId: string) {
  const res = await fetch(`${API}/api/story/projects/${projectId}/roughcut`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force: true }),
  });
  return j<{ job_id: string; status: string }>(res);
}

export async function getJob(jobId: string): Promise<Job> {
  const res = await fetch(`${API}/api/jobs/${jobId}`);
  return j<Job>(res);
}

export async function historyOverview() {
  const res = await fetch(`${API}/api/history/overview`);
  return j<{
    searches: SearchHistoryItem[];
    saved_searches: SearchHistoryItem[];
    event_counts: Record<string, number>;
    recent_clips: { story_clip_id: string; segment_id: string; project_name: string | null; created_at: string }[];
  }>(res);
}

export async function restoreSearch(searchId: string): Promise<SearchResponse> {
  const res = await fetch(`${API}/api/searches/${searchId}`);
  return j<SearchResponse>(res);
}

export async function saveSearch(searchId: string, label?: string) {
  const res = await fetch(
    `${API}/api/searches/${searchId}/save${label ? `?label=${encodeURIComponent(label)}` : ""}`,
    { method: "POST" },
  );
  return j<{ is_saved: boolean }>(res);
}

export async function explorePoints(refresh = false) {
  const res = await fetch(`${API}/api/explore/points?refresh=${refresh}`);
  return j<{
    count: number;
    bounds: { min_x: number; max_x: number; min_y: number; max_y: number };
    points: ExplorePoint[];
  }>(res);
}

/* ---------------- Visual Content Universe ---------------- */

export type UniverseSub = {
  id: string;
  label: string;
  keywords: string[];
  count: number;
  cx: number;
  cy: number;
  radius: number;
};

export type UniverseGalaxy = {
  id: string;
  label: string;
  keywords: string[];
  count: number;
  hue: number;
  cx: number;
  cy: number;
  radius: number;
  subclusters: UniverseSub[];
};

export type UniversePoint = { id: string; g: number; s: number; x: number; y: number; z: number };

export type UniverseDoc = {
  version: number;
  model: string;
  count: number;
  galaxy_count: number;
  subcluster_count: number;
  silhouette: number;
  chosen_k: number;
  created_at: string;
  generator: string;
  galaxies: UniverseGalaxy[];
  points: UniversePoint[];
};

export type GalaxySegment = {
  segment_id: string;
  x: number;
  y: number;
  z: number;
  s: number;
  start_ms: number;
  end_ms: number;
  start_clock: string;
  end_clock: string;
  duration_ms: number | null;
  description: string | null;
  video_filename: string;
  thumbnail_url: string | null;
  preview_endpoint: string;
};

export type UniverseHighlight = {
  query: string;
  galaxies: { id: string; score: number }[];
  subclusters: { id: string; score: number }[];
  segments: { segment_id: string; score: number }[];
};

export async function fetchUniverse(): Promise<UniverseDoc> {
  const res = await fetch(`${API}/api/explore/universe`);
  return j<UniverseDoc>(res);
}

export async function fetchGalaxy(gid: string): Promise<{
  galaxy: UniverseGalaxy;
  points: GalaxySegment[];
}> {
  const res = await fetch(`${API}/api/explore/universe/galaxies/${gid}`);
  return j(res);
}

export async function highlightUniverse(query: string, topK = 30): Promise<UniverseHighlight> {
  const res = await fetch(`${API}/api/explore/universe/highlight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: topK }),
  });
  return j<UniverseHighlight>(res);
}

export async function analyzeScript(script: string, topK = 5) {
  const res = await fetch(`${API}/api/script/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script, top_k: topK }),
  });
  return j<Analysis>(res);
}

export async function selectBeatSegment(beatId: string, segmentId: string) {
  const res = await fetch(`${API}/api/script/beats/${beatId}/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: segmentId }),
  });
  return j<{ selected_segment_id: string }>(res);
}

export async function logEvent(eventType: string, payload: Record<string, unknown>) {
  const res = await fetch(`${API}/api/history/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_type: eventType, payload }),
  });
  return j<{ stored: boolean }>(res);
}

export const STORY_KEY = "reelmind.story.projectId";

export async function ensureStoryProject(): Promise<string> {
  if (typeof window === "undefined") throw new Error("no window");
  const existing = localStorage.getItem(STORY_KEY);
  if (existing) {
    try {
      await getProject(existing);
      return existing;
    } catch {
      localStorage.removeItem(STORY_KEY);
    }
  }
  const list = await listProjects();
  const reusable = list.projects[0];
  if (reusable) {
    localStorage.setItem(STORY_KEY, reusable.project_id);
    return reusable.project_id;
  }
  const created = await createProject("My Story");
  localStorage.setItem(STORY_KEY, created.project_id);
  return created.project_id;
}
