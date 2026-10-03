"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  StoryProject,
  addClip,
  createProject,
  deleteClip,
  generateRoughCut,
  getJob,
  getProject,
  listProjects,
  previewUrl,
  reorderClips,
  thumbUrl,
  updateClip,
  STORY_KEY,
} from "@/lib/api";

type ProjectLite = { project_id: string; name: string; clip_count: number };

function fmt(ms: number) {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function StoryPage() {
  const [projects, setProjects] = useState<ProjectLite[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [proj, setProj] = useState<StoryProject | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<{ status: string; progress: number; error?: string | null; url?: string } | null>(null);
  const [playerSrc, setPlayerSrc] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProjects = useCallback(async (keep?: string) => {
    try {
      const list = await listProjects();
      setProjects(list.projects);
      const id = keep || localStorage.getItem(STORY_KEY) || list.projects[0]?.project_id || "";
      if (id) {
        setProjectId(id);
        localStorage.setItem(STORY_KEY, id);
        setProj(await getProject(id));
      }
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  const pick = async (id: string) => {
    setProjectId(id);
    localStorage.setItem(STORY_KEY, id);
    setPlayerSrc(null);
    setJobStatus(null);
    setProj(await getProject(id));
  };

  const newProject = async () => {
    const name = prompt("Project name", "My Story");
    if (!name) return;
    const p = await createProject(name);
    await loadProjects(p.project_id);
  };

  const refresh = async () => {
    if (projectId) setProj(await getProject(projectId));
  };

  const patch = async (clipId: string, body: Record<string, unknown>) => {
    try {
      await updateClip(clipId, body);
      await refresh();
    } catch (e) {
      setErr(String(e));
    }
  };

  const move = async (idx: number, dir: -1 | 1) => {
    if (!proj) return;
    const ids = proj.clips.map((c) => c.story_clip_id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    await reorderClips(proj.project_id, ids);
    await refresh();
  };

  const remove = async (clipId: string) => {
    await deleteClip(clipId);
    await refresh();
  };

  const addFromPaste = async () => {
    const seg = prompt("Paste a segment id (video:start-end) to add:");
    if (!seg || !projectId) return;
    try {
      await addClip(projectId, seg.trim());
      await refresh();
      setNotice("Clip added.");
    } catch (e) {
      setErr(String(e));
    }
  };

  const roughcut = async () => {
    if (!proj) return;
    setErr(null);
    setNotice(null);
    setPlayerSrc(null);
    try {
      const j = await generateRoughCut(proj.project_id);
      setJobId(j.job_id);
      setJobStatus({ status: j.status, progress: 0 });
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const st = await getJob(j.job_id);
          setJobStatus({
            status: st.status,
            progress: st.progress ?? 0,
            error: st.error,
            url:
              typeof st.detail?.output_url === "string"
                ? st.detail.output_url
                : undefined,
          });
          if (st.status === "done" || st.status === "failed" || st.status === "cancelled") {
            if (pollRef.current) clearInterval(pollRef.current);
            if (st.status === "done") setNotice("Rough cut ready below.");
            if (st.status === "failed") setErr(st.error || "render failed");
          }
        } catch (e) {
          setErr(String(e));
        }
      }, 1500);
    } catch (e) {
      setErr(String(e));
    }
  };

  const previewClip = async (segmentId: string) => {
    try {
      setPlayerSrc(await previewUrl(segmentId));
    } catch (e) {
      setErr(String(e));
    }
  };

  const total = proj?.clips.reduce((a, c) => a + c.duration_ms, 0) ?? 0;

  return (
    <div>
      <div className="eyebrow">STORYBOARD</div>
      <h1>
        Your story, <span className="dim">taking shape.</span>
      </h1>
      <p className="sub">Arrange clips, trim, reorder, render a rough cut with FFmpeg.</p>

      <div className="row" style={{ marginBottom: 14 }}>
        <select value={projectId} onChange={(e) => pick(e.target.value)}>
          {projects.length === 0 && <option value="">(no projects)</option>}
          {projects.map((p) => (
            <option key={p.project_id} value={p.project_id}>
              {p.name} ({p.clip_count})
            </option>
          ))}
        </select>
        <button className="ghost" onClick={newProject}>
          + New project
        </button>
        <button className="ghost" onClick={addFromPaste} disabled={!projectId}>
          + Add by ID
        </button>
        <button className="ghost" onClick={() => (window.location.href = "/script")}>
          ✦ Analyze story
        </button>
        <button className="dark" onClick={roughcut} disabled={!proj || proj.clips.length === 0 || busy}>
          ▶ Generate rough cut ↗
        </button>
        {proj && (
          <span className="muted small">
            {proj.clips.length} clips · {fmt(total)}
          </span>
        )}
      </div>

      {err && <div className="error">{err}</div>}
      {notice && <div className="notice">{notice}</div>}

      {jobStatus && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>
              Job: <b>{jobStatus.status}</b>
            </span>
            <span className="muted small">{Math.round((jobStatus.progress ?? 0) * 100)}%</span>
          </div>
          <div className="progress" style={{ marginTop: 8 }}>
            <div style={{ width: `${Math.round((jobStatus.progress ?? 0) * 100)}%` }} />
          </div>
        </div>
      )}

      {playerSrc && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <video key={playerSrc} src={playerSrc} controls autoPlay style={{ width: "100%", borderRadius: 8 }} />
        </div>
      )}

      {proj && proj.clips.length > 0 && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <div className="muted small">Timeline</div>
          <div className="timeline">
            {proj.clips.map((c, i) => (
              <div
                key={c.story_clip_id}
                className="seg"
                style={{ flex: c.duration_ms }}
                title={`${c.description ?? ""} (${fmt(c.duration_ms)})`}
              >
                #{i + 1} {fmt(c.duration_ms)}
              </div>
            ))}
          </div>
        </div>
      )}

      {proj?.clips.map((c, i) => {
        const thumb = thumbUrl(c.thumbnail_url);
        const maxMs = c.timeline_end_ms - c.timeline_start_ms + (c.source_end_ms - c.source_start_ms);
        return (
          <div className="clip-row" key={c.story_clip_id}>
            <div className="clip-info" style={{ display: "flex", gap: 14, alignItems: "center" }}>
              {thumb ? (
                <img className="clip-thumb" src={thumb} alt="" />
              ) : (
                <div className="clip-thumb" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                  🎬
                </div>
              )}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="desc">{c.description || c.segment_id}</div>
                <div className="meta small">
                  #{i + 1} · {c.video_filename} · source {c.source_start_clock}→{c.source_end_clock} ·{" "}
                  {fmt(c.duration_ms)}
                </div>
                <div className="row" style={{ marginTop: 6 }}>
                  <label className="muted small">trim start</label>
                  <input
                    type="number"
                    step={100}
                    min={0}
                    max={Math.max(0, c.source_end_ms - 400)}
                    value={c.source_start_ms}
                    onChange={(e) => patch(c.story_clip_id, { source_start_ms: Number(e.target.value) })}
                    style={{ width: 100, padding: "5px 7px" }}
                  />
                  <label className="muted small">end</label>
                  <input
                    type="number"
                    step={100}
                    min={c.source_start_ms + 400}
                    max={maxMs}
                    value={c.source_end_ms}
                    onChange={(e) => patch(c.story_clip_id, { source_end_ms: Number(e.target.value) })}
                    style={{ width: 100, padding: "5px 7px" }}
                  />
                </div>
              </div>
            </div>
            <div className="row">
              <button className="small ghost" onClick={() => move(i, -1)} disabled={i === 0}>
                ↑
              </button>
              <button className="small ghost" onClick={() => move(i, 1)} disabled={i === proj.clips.length - 1}>
                ↓
              </button>
              <button className="small ghost" onClick={() => previewClip(c.segment_id)}>
                ▶
              </button>
              <button className="small ghost" onClick={() => remove(c.story_clip_id)}>
                ✕
              </button>
            </div>
          </div>
        );
      })}

      {proj && proj.clips.length === 0 && (
        <div className="panel muted">No clips yet — add some from AI Brain search results.</div>
      )}
    </div>
  );
}
