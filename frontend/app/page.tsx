"use client";

import { useEffect, useRef, useState } from "react";
import ResultCard from "@/components/ResultCard";
import { API, SearchResponse, findSimilar, postSearch, saveSearch, thumbUrl } from "@/lib/api";

function fmt(ms: number) {
  const s = Math.round((ms || 0) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

const EXAMPLES = [
  "a slow Sunday morning",
  "Lonely in a big city",
  "Chasing a dream",
  "warm golden hour light",
  "someone driving through traffic",
  "person working late at night",
];

export default function AIBrain() {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(12);
  const [minScore, setMinScore] = useState(0.2);
  const [lang, setLang] = useState("");
  const [dur, setDur] = useState("");
  const [busy, setBusy] = useState(false);
  const [resp, setResp] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [indexed, setIndexed] = useState(0);
  const [dock, setDock] = useState<{ id: string; desc: string; thumb: string | null; dur: string; file: string }[]>([]);
  const [dockProject, setDockProject] = useState("Your story");
  const resultsRef = useRef<HTMLDivElement>(null);

  const loadDock = async () => {
    try {
      const { listProjects, getProject } = await import("@/lib/api");
      const list = await listProjects();
      const id = localStorage.getItem("reelmind.story.projectId");
      const lite = list.projects.find((p) => p.project_id === id) ?? list.projects[0];
      if (!lite) {
        setDock([]);
        return;
      }
      setDockProject(lite.name);
      const full = await getProject(lite.project_id);
      setDock(
        full.clips.map((c) => ({
          id: c.story_clip_id,
          desc: c.description || c.segment_id,
          thumb: thumbUrl(c.thumbnail_url),
          dur: fmt(c.duration_ms),
          file: c.video_filename,
        })),
      );
    } catch {
      /* api down */
    }
  };

  const search = async (q?: string) => {
    const text = (q ?? query).trim();
    if (!text) return;
    setBusy(true);
    setErr(null);
    setNotice(null);
    try {
      const filters: Record<string, unknown> = {};
      if (minScore > 0) filters.min_score = minScore;
      if (lang) filters.language = lang;
      if (dur === "lt10") filters.duration_ms = { max: 10000 };
      if (dur === "10to20") filters.duration_ms = { min: 10000, max: 20000 };
      if (dur === "gt20") filters.duration_ms = { min: 20000 };
      const r = await postSearch(text, topK, filters);
      if (q) setQuery(q);
      setResp(r);
      if (r.result_count === 0)
        setNotice(`No frames for "${r.query}" — try different words or lower the min-score.`);
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const similar = async (segmentId: string) => {
    setBusy(true);
    setErr(null);
    try {
      setResp(await findSimilar(segmentId, topK));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!resp) return;
    try {
      await saveSearch(resp.search_id, resp.query);
      setNotice("Search saved — visible in Search history.");
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    loadDock();
    (async () => {
      try {
        const h = await fetch(`${API}/api/health`).then((r) => r.json());
        setIndexed(h.qdrant?.points ?? 0);
      } catch {
        /* ignore */
      }
    })();
    try {
      const raw = sessionStorage.getItem("reelmind.lastSearch");
      if (raw) {
        const r = JSON.parse(raw) as SearchResponse;
        setResp(r);
        setQuery(r.query);
        sessionStorage.removeItem("reelmind.lastSearch");
        return;
      }
    } catch {
      /* ignore */
    }
    const q = new URLSearchParams(window.location.search).get("q");
    if (q) search(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const concepts = resp?.understanding.concepts.slice(0, 3) ?? [];

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div className="eyebrow">YOUR IDEAS, IN FOCUS.</div>
          <h1>
            Find the feeling. <span className="dim">Build the story.</span>
          </h1>
          <p className="sub">B-roll that understands your story, not just your keywords.</p>
        </div>
        <div className="panel" style={{ padding: "12px 16px", minWidth: 190 }}>
          <div className="row" style={{ gap: 8 }}>
            <span className="saved-dot" />
            <b style={{ fontSize: 13 }}>Library ready</b>
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>
            {indexed.toLocaleString()} curated frames
          </div>
        </div>
      </div>

      <div className="search-bar">
        <div className="search-shell">
          <span className="mag">⌕</span>
          <input
            type="text"
            placeholder="slow mornings, warm light, everyday moments…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <span className="kbd">⌘ K</span>
        </div>
        <button className="btn-find" onClick={() => search()} disabled={busy}>
          {busy ? <span className="spinner" /> : "✦ Find frames →"}
        </button>
      </div>

      <div className="inspo">
        <span>A little inspiration</span>
        {EXAMPLES.slice(0, 3).map((ex) => (
          <button key={ex} className="chip" onClick={() => search(ex)}>
            {ex} ↗
          </button>
        ))}
      </div>

      <div className="frame-intel">
        <div className="fi-logo">✦</div>
        <div>
          <div className="fi-title">FRAME INTELLIGENCE</div>
          <div className="fi-msg">
            {resp ? `Feeling: ${resp.query}.` : "We’re picking up the feeling."}
          </div>
        </div>
        <div className="fi-col">
          <div className="k">SCENE</div>
          <div className="v">{concepts[0] || "Any scene"}</div>
        </div>
        <div className="fi-col">
          <div className="k">MOOD</div>
          <div className="v">{concepts[1] || resp?.query_type || "Semantic"}</div>
        </div>
        <div className="fi-col">
          <div className="k">LIGHT</div>
          <div className="v">{concepts[2] || "Natural"}</div>
        </div>
        <div style={{ marginLeft: "auto" }} className="fi-col">
          <div className="k">MODE</div>
          <div className="v">Semantic vector search</div>
        </div>
      </div>

      <div className="filters">
        <label>
          Results
          <input
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value) || 12)}
            style={{ width: 64 }}
          />
        </label>
        <label>
          Min score
          <input
            type="number"
            step="0.05"
            min={0}
            max={1}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            style={{ width: 76 }}
          />
        </label>
        <label>
          Duration
          <select value={dur} onChange={(e) => setDur(e.target.value)}>
            <option value="">any</option>
            <option value="lt10">&lt; 10s</option>
            <option value="10to20">10–20s</option>
            <option value="gt20">&gt; 20s</option>
          </select>
        </label>
        {resp && (
          <button className="small ghost" onClick={save}>
            ☆ Save search
          </button>
        )}
      </div>

      {err && <div className="error">{err}</div>}
      {notice && <div className="notice">{notice}</div>}

      <div ref={resultsRef}>
        {resp && (
          <>
            <div className="results-head" style={{ marginTop: 24 }}>
              <div>
                <h2 style={{ margin: 0 }}>Made for your story.</h2>
                <div className="results-meta">
                  {resp.result_count} matching frames
                  {resp.latency_ms ? ` · ${(resp.latency_ms / 1000).toFixed(2)}s` : ""}
                </div>
              </div>
              <div className="row">
                <div className="toggle-group">
                  <button className="on">✦ Semantic</button>
                  <button disabled title="no keyword matching — local vectors only">Keyword</button>
                </div>
                <div className="toggle-group">
                  <button className="on">What AI sees</button>
                </div>
              </div>
            </div>
            <div className="chips" style={{ marginTop: 10 }}>
              {resp.understanding.concepts.map((c) => (
                <span key={c} className="chip hi">
                  {c}
                </span>
              ))}
              <span className="chip">{resp.score_note}</span>
            </div>
          </>
        )}
        {!resp && !busy && (
          <div className="results-head" style={{ marginTop: 24 }}>
            <div>
              <h2 style={{ margin: 0 }}>Made for your story.</h2>
              <div className="results-meta">Search above to light up the reel.</div>
            </div>
          </div>
        )}
        {resp && (
          <div className="grid">
            {resp.results.map((r) => (
              <ResultCard key={r.segment_id} r={r} onSimilar={similar} onAdded={loadDock} />
            ))}
          </div>
        )}
      </div>

      <div className="story-dock">
        <div className="story-dock-head">
          <div>
            <div className="t">Your story, taking shape</div>
            <div className="s">{dockProject}</div>
          </div>
          <div className="row">
            <button className="ghost small" onClick={() => (window.location.href = "/story")}>
              ✦ Analyze story
            </button>
            <button className="small" onClick={() => (window.location.href = "/story")}>
              ▶ Generate rough cut ↗
            </button>
          </div>
        </div>
        <div className="dock-strip">
          {dock.map((c, i) => (
            <div className="dock-clip" key={c.id}>
              {c.thumb ? <img src={c.thumb} alt="" /> : <div style={{ width: 52, height: 32, background: "var(--panel-3)", borderRadius: 6 }} />}
              <div style={{ minWidth: 0 }}>
                <div className="d">
                  {String(i + 1).padStart(2, "0")} · {c.desc}
                </div>
                <div className="m">
                  {c.file} · {c.dur}
                </div>
              </div>
            </div>
          ))}
          <button className="dock-add" onClick={() => (window.location.href = "/story")}>
            + Next frame
          </button>
        </div>
      </div>
    </div>
  );
}
