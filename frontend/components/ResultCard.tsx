"use client";

import { useState } from "react";
import {
  SearchResult,
  addClip,
  ensureStoryProject,
  previewUrl,
  thumbUrl,
} from "@/lib/api";

function dur(ms: number) {
  const s = Math.round((ms || 0) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export default function ResultCard({
  r,
  onSimilar,
  onAdded,
}: {
  r: SearchResult;
  onSimilar?: (segmentId: string) => void;
  onAdded?: () => void;
}) {
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [added, setAdded] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const play = async () => {
    if (playing) return;
    setPlaying(true);
    setLoading(true);
    setErr(null);
    try {
      setVideoSrc(await previewUrl(r.segment_id));
    } catch (e) {
      setErr(String(e));
      setPlaying(false);
    } finally {
      setLoading(false);
    }
  };

  const add = async () => {
    setErr(null);
    try {
      const pid = await ensureStoryProject();
      await addClip(pid, r.segment_id);
      setAdded(true);
      onAdded?.();
    } catch (e) {
      setErr(String(e));
    }
  };

  const match = Math.round((r.score ?? 0) * 100);
  const thumb = thumbUrl(r.thumbnail_url);
  const res = r.width && r.height ? (r.width >= 1280 ? "HD" : `${r.width}×${r.height}`) : "SD";
  const sub = (r.why_match.length ? r.why_match : r.concepts).slice(0, 2).join(" · ");

  return (
    <div className="card">
      <div className="card-media">
        {videoSrc ? (
          <video src={videoSrc} controls autoPlay />
        ) : thumb ? (
          <img src={thumb} alt="" />
        ) : (
          <div className="placeholder">🎬</div>
        )}
        <span className="badge">✦ {match}% match</span>
        <button className={`bookmark ${added ? "on" : ""}`} onClick={add} disabled={added} title="Add to story">
          {added ? "✓" : "＋"}
        </button>
        <span className="resolution-tag">{res}</span>
        <span className="clock">{dur(r.duration_ms ?? r.end_ms - r.start_ms)}</span>
        {!videoSrc &&
          (loading ? (
            <span className="play-btn">
              <span className="spinner" />
            </span>
          ) : (
            <button className="play-btn" onClick={play} aria-label="preview">
              ▶
            </button>
          ))}
      </div>
      <div className="card-body">
        <div className="card-title-row">
          <div className="card-desc">{r.description}</div>
          <button className={`card-add ${added ? "on" : ""}`} onClick={add} disabled={added} title="Add to story">
            {added ? "✓" : "+"}
          </button>
        </div>
        <div className="card-sub">
          <span>{sub || "semantic match"}</span>
          <span className="meta">
            {r.start_clock}–{r.end_clock}
          </span>
        </div>
        {err && <div className="error small">{err}</div>}
        <div className="card-actions">
          <button className="small ghost" onClick={play}>
            ▶ Preview
          </button>
          {onSimilar && (
            <button className="small ghost" onClick={() => onSimilar(r.segment_id)}>
              ✦ Find similar
            </button>
          )}
          <span className="meta" style={{ marginLeft: "auto" }}>
            {r.video_filename}
          </span>
        </div>
      </div>
    </div>
  );
}
