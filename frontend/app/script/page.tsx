"use client";

import { useEffect, useState } from "react";
import ResultCard from "@/components/ResultCard";
import {
  Analysis,
  addClip,
  analyzeScript,
  ensureStoryProject,
  selectBeatSegment,
  thumbUrl,
} from "@/lib/api";

export default function ScriptPage() {
  const [script, setScript] = useState("");
  const [busy, setBusy] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [added, setAdded] = useState(false);
  const [clips, setClips] = useState(0);

  const run = async () => {
    if (!script.trim()) return;
    setBusy(true);
    setErr(null);
    setAdded(false);
    try {
      const a = await analyzeScript(script, 5);
      setAnalysis(a);
      const sel: Record<string, string> = {};
      a.beats.forEach((b) => {
        if (b.selected_segment_id) sel[b.beat_id] = b.selected_segment_id;
        else if (b.candidates.length) sel[b.beat_id] = b.candidates[0].segment_id;
      });
      setSelected(sel);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const pick = async (beatId: string, segmentId: string) => {
    setSelected((s) => ({ ...s, [beatId]: segmentId }));
    try {
      await selectBeatSegment(beatId, segmentId);
    } catch {
      /* non-fatal */
    }
  };

  const addSelected = async () => {
    setErr(null);
    try {
      const pid = await ensureStoryProject();
      for (const [beatId, segId] of Object.entries(selected)) {
        if (segId) await addClip(pid, segId);
        void beatId;
      }
      setAdded(true);
      setClips((c) => c + Object.keys(selected).length);
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    setClips((c) => c);
  }, []);

  return (
    <div>
      <div className="eyebrow">SCRIPT STUDIO</div>
      <h1>
        Write it. <span className="dim">We’ll find the frames.</span>
      </h1>
      <p className="sub">
        Paste a script or outline — it gets split into beats, each beat finds matching b-roll.
      </p>

      <div className="panel">
        <textarea
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder={
            "Paste your script here…\n\nEvery night thousands of developers work late.\nRain falls on the busy city street.\nThe team celebrates a successful launch."
          }
          style={{ minHeight: 140 }}
        />
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={run} disabled={busy || !script.trim()}>
            {busy ? <span className="spinner" /> : "Analyze script"}
          </button>
          {analysis && (
            <button className="good" onClick={addSelected} disabled={added}>
              {added ? "✓ Added to story" : `+ Add ${Object.keys(selected).length} clips to story`}
            </button>
          )}
          <span className="muted small">Story clips added: {clips}</span>
        </div>
      </div>

      {err && <div className="error">{err}</div>}

      {analysis && (
        <>
          <h2>
            {analysis.beat_count} beats
            <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>
              {" "}
              · status {analysis.status}
            </span>
          </h2>
          {analysis.beats.map((b) => (
            <div className="beat" key={b.beat_id}>
              <div className="beat-head">
                <span className="row">
                  <span className="beat-index">Beat {b.beat_index + 1}</span>
                  <b>{b.text}</b>
                </span>
                <span className="muted small">{b.visual_intent}</span>
              </div>
              <div className="cand-row" style={{ marginTop: 10 }}>
                {b.candidates.map((c) => (
                  <div
                    key={c.segment_id}
                    className={`cand ${selected[b.beat_id] === c.segment_id ? "selected" : ""}`}
                    onClick={() => pick(b.beat_id, c.segment_id)}
                    style={{ cursor: "pointer" }}
                  >
                    {thumbUrl(c.thumbnail_url) ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={thumbUrl(c.thumbnail_url)!} alt="" />
                    ) : (
                      <div style={{ height: 110, display: "flex", alignItems: "center", justifyContent: "center" }}>
                        🎬
                      </div>
                    )}
                    <div className="body">
                      <span className="score">{c.score.toFixed(3)}</span>
                      <span className="small">{c.description}</span>
                      <span className="meta small">
                        {c.start_clock}–{c.end_clock}
                      </span>
                    </div>
                  </div>
                ))}
                {b.candidates.length === 0 && <span className="muted small">no candidates</span>}
              </div>
            </div>
          ))}

          <h2>Full results</h2>
          <div className="grid">
            {analysis.beats.flatMap((b) =>
              b.candidates.map((c) => (
                <ResultCard key={`${b.beat_id}:${c.segment_id}`} r={c} />
              )),
            )}
          </div>
        </>
      )}
    </div>
  );
}
