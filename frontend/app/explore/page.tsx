"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  GalaxySegment,
  UniverseDoc,
  UniverseGalaxy,
  UniverseHighlight,
  UniverseSub,
  addClip,
  ensureStoryProject,
  fetchGalaxy,
  fetchUniverse,
  highlightUniverse,
  previewUrl,
} from "@/lib/api";

type TT =
  | { kind: "galaxy"; g: UniverseGalaxy; sx: number; sy: number; score?: number }
  | { kind: "sub"; g: UniverseGalaxy; sub: UniverseSub; sx: number; sy: number; score?: number }
  | { kind: "point"; p: GalaxySegment; sx: number; sy: number };

type HlSets = {
  gal: Set<string>;
  sub: Set<string>;
  seg: Set<string>;
  galScore: Map<string, number>;
  subScore: Map<string, number>;
  active: boolean;
};
const NO_HL: HlSets = {
  gal: new Set(), sub: new Set(), seg: new Set(),
  galScore: new Map(), subScore: new Map(), active: false,
};

type Controls = {
  reset: () => void;
  zoomBy: (f: number) => void;
  back: () => void;
  enter: (gid: string) => void;
};

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
const tipId = (t: TT | null) =>
  !t ? "" : t.kind === "point" ? t.p.segment_id : t.kind === "galaxy" ? t.g.id : t.sub.id;

export default function ExploreGalaxyPage() {
  const [doc, setDoc] = useState<UniverseDoc | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [crumb, setCrumb] = useState<UniverseGalaxy | null>(null);
  const [tooltip, setTooltip] = useState<TT | null>(null);
  const [selected, setSelected] = useState<GalaxySegment | null>(null);
  const [video, setVideo] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addedMsg, setAddedMsg] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [hl, setHl] = useState<UniverseHighlight | null>(null);
  const [hlBusy, setHlBusy] = useState(false);
  const [controls, setControls] = useState<Controls | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const hlRef = useRef<HlSets>(NO_HL);
  const hlResultRef = useRef<UniverseHighlight | null>(null);

  /* ---------------- data ---------------- */
  const loadDoc = useCallback(async () => {
    setErr(null);
    try {
      setDoc(await fetchUniverse());
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    loadDoc();
  }, [loadDoc]);

  useEffect(() => {
    let live = true;
    setVideo(null);
    if (selected) {
      previewUrl(selected.segment_id)
        .then((u) => live && setVideo(u))
        .catch(() => live && setVideo(null));
    }
    return () => {
      live = false;
    };
  }, [selected]);

  /* ---------------- semantic highlight ---------------- */
  const runHighlight = async () => {
    const text = q.trim();
    if (!text) return;
    setHlBusy(true);
    setErr(null);
    try {
      const res = await highlightUniverse(text);
      setHl(res);
      hlResultRef.current = res;
      const top = res.galaxies[0]?.score ?? 0;
      const gal = new Set(
        res.galaxies.filter((g) => g.score >= top - 0.14 && g.score > 0.2).map((g) => g.id),
      );
      const sub = new Set(res.subclusters.filter((s) => s.score > 0.28).map((s) => s.id));
      const seg = new Set(res.segments.map((s) => s.segment_id));
      hlRef.current = {
        gal,
        sub,
        seg,
        active: true,
        galScore: new Map(res.galaxies.map((g) => [g.id, g.score])),
        subScore: new Map(res.subclusters.map((s) => [s.id, s.score])),
      };
    } catch (e) {
      setErr(String(e));
    } finally {
      setHlBusy(false);
    }
  };

  const clearHighlight = () => {
    setHl(null);
    hlResultRef.current = null;
    hlRef.current = NO_HL;
  };

  const addToStory = async () => {
    if (!selected) return;
    setAdding(true);
    try {
      const pid = await ensureStoryProject();
      await addClip(pid, selected.segment_id);
      setAddedMsg("Added to story ✓");
    } catch (e) {
      setErr(String(e));
    } finally {
      setAdding(false);
    }
  };

  /* ---------------- canvas engine ---------------- */
  useEffect(() => {
    const cv = canvasRef.current;
    const wrap = wrapRef.current;
    if (!cv || !wrap || !doc) return;

    const ctx = cv.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let W = 0;
    let H = 0;
    const cam = { x: 0, y: 0, z: 0.2 };
    const targ = { x: 0, y: 0, z: 0.2 };
    let mode: "universe" | "galaxy" = "universe";
    let gid: string | null = null;
    let uniFitZ = 0.2;
    let raf = 0;
    let disposed = false;

    /* group points by galaxy once */
    const ptsByG: GalaxySegLite[][] = doc.galaxies.map(() => []);
    type GalaxySegLite = UniverseDoc["points"][number];
    doc.points.forEach((p) => {
      if (p.g >= 0 && p.g < ptsByG.length) ptsByG[p.g].push(p);
    });

    const galPts = new Map<string, GalaxySegment[]>();
    const galPending = new Map<string, Promise<GalaxySegment[]>>();
    let hover: TT | null = null;
    let pendingHover: { id: string; g: string; sx: number; sy: number } | null = null;
    let drag: { sx: number; sy: number; tx: number; ty: number; moved: boolean } | null = null;

    /* hue sprite cache */
    const sprites = new Map<number, HTMLCanvasElement>();
    const sprite = (hue: number) => {
      const key = Math.round(hue / 6) * 6;
      let c = sprites.get(key);
      if (!c) {
        c = document.createElement("canvas");
        c.width = c.height = 128;
        const g2 = c.getContext("2d")!;
        const grad = g2.createRadialGradient(64, 64, 0, 64, 64, 64);
        grad.addColorStop(0, `hsla(${key}, 78%, 66%, 0.95)`);
        grad.addColorStop(0.35, `hsla(${key}, 72%, 55%, 0.4)`);
        grad.addColorStop(1, `hsla(${key}, 70%, 50%, 0)`);
        g2.fillStyle = grad;
        g2.fillRect(0, 0, 128, 128);
        sprites.set(key, c);
      }
      return c;
    };

    const w2sx = (x: number) => (x - cam.x) * cam.z + W / 2;
    const w2sy = (y: number) => (y - cam.y) * cam.z + H / 2;
    const s2wx = (sx: number) => (sx - W / 2) / cam.z + cam.x;
    const s2wy = (sy: number) => (sy - H / 2) / cam.z + cam.y;
    const galIdxOf = (id: string) => parseInt(id.slice(1), 10);

    const fitUniverse = () => {
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const g of doc.galaxies) {
        minX = Math.min(minX, g.cx - g.radius);
        maxX = Math.max(maxX, g.cx + g.radius);
        minY = Math.min(minY, g.cy - g.radius);
        maxY = Math.max(maxY, g.cy + g.radius);
      }
      const spanX = Math.max(maxX - minX, 1);
      const spanY = Math.max(maxY - minY, 1);
      uniFitZ = Math.min(W / (spanX * 1.14), H / (spanY * 1.18));
      targ.x = (minX + maxX) / 2;
      targ.y = (minY + maxY) / 2;
      targ.z = uniFitZ;
    };

    const fitGalaxy = (g: UniverseGalaxy) => {
      targ.x = g.cx;
      targ.y = g.cy;
      targ.z = clamp(Math.min(W, H) / (2 * g.radius * 1.3), 0.05, 40);
    };

    const ensureGal = (id: string): Promise<GalaxySegment[]> => {
      const have = galPts.get(id);
      if (have) return Promise.resolve(have);
      let p = galPending.get(id);
      if (!p) {
        p = fetchGalaxy(id)
          .then((r) => {
            galPts.set(id, r.points);
            return r.points;
          })
          .catch((e) => {
            galPending.delete(id);
            throw e;
          });
        galPending.set(id, p);
      }
      return p;
    };

    const enterGalaxy = async (id: string) => {
      const g = doc.galaxies.find((x) => x.id === id);
      if (!g) return;
      mode = "galaxy";
      gid = id;
      setCrumb(g);
      fitGalaxy(g);
      try {
        const pts = await ensureGal(id);
        if (pendingHover && pendingHover.g === id && !disposed) {
          const p = pts.find((x) => x.segment_id === pendingHover!.id);
          if (p) {
            hover = { kind: "point", p, sx: pendingHover.sx, sy: pendingHover.sy };
            setTooltip(hover);
          }
          pendingHover = null;
        }
      } catch (e) {
        setErr(String(e));
      }
    };

    const exitGalaxy = () => {
      mode = "universe";
      gid = null;
      setCrumb(null);
      setSelected(null);
      fitUniverse();
    };

    const activeGal = () => (gid ? doc.galaxies.find((g) => g.id === gid) : null);

    /* ---------------- camera ---------------- */
    const toCam = (dt: number) => {
      const k = 1 - Math.exp(-dt / 0.14);
      cam.x += (targ.x - cam.x) * k;
      cam.y += (targ.y - cam.y) * k;
      if (cam.z > 1e-9 && targ.z > 1e-9) {
        cam.z *= Math.exp(Math.log(targ.z / cam.z) * k);
      }
    };

    const zoomAt = (f: number, mx: number, my: number) => {
      const nz = clamp(targ.z * f, 0.02, 60);
      const wx = (mx - W / 2) / targ.z + targ.x;
      const wy = (my - H / 2) / targ.z + targ.y;
      targ.x = wx - (mx - W / 2) / nz;
      targ.y = wy - (my - H / 2) / nz;
      targ.z = nz;
    };

    /* ---------------- hit testing ---------------- */
    const galaxyAt = (wx: number, wy: number): UniverseGalaxy | null => {
      for (const g of doc.galaxies) {
        const dx = wx - g.cx;
        const dy = wy - g.cy;
        if (dx * dx + dy * dy <= g.radius * g.radius) return g;
      }
      return null;
    };

    const subAt = (wx: number, wy: number): UniverseSub | null => {
      const g = activeGal();
      if (!g) return null;
      for (const s of g.subclusters) {
        const dx = wx - s.cx;
        const dy = wy - s.cy;
        if (dx * dx + dy * dy <= s.radius * s.radius) return s;
      }
      return null;
    };

    const pointsHittable = () =>
      mode === "galaxy" || cam.z > uniFitZ * 1.5;

    const docPointAt = (mx: number, my: number): UniverseDoc["points"][number] | null => {
      if (!pointsHittable()) return null;
      let best: UniverseDoc["points"][number] | null = null;
      let bestD = 10;
      const gFilter = mode === "galaxy" && gid ? galIdxOf(gid) : -1;
      for (const p of doc.points) {
        if (gFilter >= 0 && p.g !== gFilter) continue;
        const dx = w2sx(p.x) - mx;
        const dy = w2sy(p.y) - my;
        const d = dx * dx + dy * dy;
        if (d < bestD * bestD) {
          bestD = Math.sqrt(d);
          best = p;
        }
      }
      return best;
    };

    const resolvePointHover = (mx: number, my: number): boolean => {
      const hit = docPointAt(mx, my);
      if (!hit) return false;
      const gidStr = `g${String(hit.g).padStart(2, "0")}`;
      const meta = galPts.get(gidStr);
      if (meta) {
        const p = meta.find((x) => x.segment_id === hit.id);
        if (p) {
          setHoverLocal({ kind: "point", p, sx: mx, sy: my });
          return true;
        }
      } else {
        pendingHover = { id: hit.id, g: gidStr, sx: mx, sy: my };
        ensureGal(gidStr)
          .then((pts) => {
            if (!pendingHover || pendingHover.id !== hit.id || disposed) return;
            const p = pts.find((x) => x.segment_id === hit.id);
            if (p) {
              hover = { kind: "point", p, sx: pendingHover.sx, sy: pendingHover.sy };
              setTooltip(hover);
            }
            pendingHover = null;
          })
          .catch(() => {
            pendingHover = null;
          });
      }
      return true;
    };

    /* ---------------- input ---------------- */
    const rel = (e: PointerEvent | WheelEvent) => {
      const r = cv.getBoundingClientRect();
      return { mx: e.clientX - r.left, my: e.clientY - r.top };
    };

    const onDown = (e: PointerEvent) => {
      const { mx, my } = rel(e);
      drag = { sx: mx, sy: my, tx: targ.x, ty: targ.y, moved: false };
      cv.setPointerCapture(e.pointerId);
    };

    const setHoverLocal = (t: TT | null) => {
      if (!t && !hover) return;
      if (t && hover && tipId(t) === tipId(hover)) {
        const moved = Math.abs(t.sx - hover.sx) + Math.abs(t.sy - hover.sy);
        hover = t;
        if (moved < 14) return;              // keep tooltip, avoid re-render storm
      } else {
        hover = t;
      }
      setTooltip(t);
    };

    const onMove = (e: PointerEvent) => {
      const { mx, my } = rel(e);
      if (drag) {
        if (Math.hypot(mx - drag.sx, my - drag.sy) > 4) drag.moved = true;
        targ.x = drag.tx - (mx - drag.sx) / targ.z;
        targ.y = drag.ty - (my - drag.sy) / targ.z;
        return;
      }
      if (resolvePointHover(mx, my)) return;

      const wx = s2wx(mx);
      const wy = s2wy(my);
      if (mode === "galaxy") {
        const g0 = activeGal();
        const sub = subAt(wx, wy);
        if (sub && g0) {
          setHoverLocal({
            kind: "sub", g: g0, sub, sx: mx, sy: my,
            score: hlRef.current.subScore.get(sub.id),
          });
          return;
        }
      }
      const g = galaxyAt(wx, wy);
      if (g) {
        setHoverLocal({
          kind: "galaxy", g, sx: mx, sy: my,
          score: hlRef.current.galScore.get(g.id),
        });
        return;
      }
      setHoverLocal(null);
    };

    const onUp = (e: PointerEvent) => {
      const { mx, my } = rel(e);
      const wasDrag = drag;
      drag = null;
      if (wasDrag?.moved) return;

      const hit = docPointAt(mx, my);
      if (hit) {
        const gidStr = `g${String(hit.g).padStart(2, "0")}`;
        const meta = galPts.get(gidStr);
        const p = meta?.find((x) => x.segment_id === hit.id);
        if (p) {
          setSelected(p);
          setAddedMsg(null);
          return;
        }
        pendingHover = { id: hit.id, g: gidStr, sx: mx, sy: my };
        ensureGal(gidStr)
          .then((pts) => {
            const p2 = pts.find((x) => x.segment_id === hit.id);
            if (p2 && !disposed) {
              setSelected(p2);
              setAddedMsg(null);
            }
          })
          .catch(() => undefined);
        return;
      }
      const wx = s2wx(mx);
      const wy = s2wy(my);
      if (mode === "galaxy") {
        const sub = subAt(wx, wy);
        if (sub) {
          targ.x = sub.cx;
          targ.y = sub.cy;
          targ.z = clamp(targ.z * 1.7, 0.05, 60);
          return;
        }
      }
      const g = galaxyAt(wx, wy);
      if (g && (!gid || g.id !== gid)) {
        void enterGalaxy(g.id);
      }
    };

    const onLeave = () => {
      drag = null;
      pendingHover = null;
      setHoverLocal(null);
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { mx, my } = rel(e);
      zoomAt(Math.exp(-e.deltaY * 0.0016), mx, my);
    };

    const onDbl = () => {
      if (mode === "galaxy") exitGalaxy();
    };

    cv.addEventListener("pointerdown", onDown);
    cv.addEventListener("pointermove", onMove);
    cv.addEventListener("pointerup", onUp);
    cv.addEventListener("pointerleave", onLeave);
    cv.addEventListener("wheel", onWheel, { passive: false });
    cv.addEventListener("dblclick", onDbl);

    /* ---------------- size ---------------- */
    const resize = () => {
      W = wrap.clientWidth;
      H = wrap.clientHeight;
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
      cv.style.width = `${W}px`;
      cv.style.height = `${H}px`;
    };
    const ro = new ResizeObserver(() => resize());
    ro.observe(wrap);
    resize();
    fitUniverse();
    cam.x = targ.x;
    cam.y = targ.y;
    cam.z = targ.z;

    /* ---------------- drawing ---------------- */
    const drawGlow = (hue: number, x: number, y: number, r: number, alpha: number) => {
      if (r < 1 || alpha <= 0.004) return;
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = clamp(alpha, 0, 1);
      const s = sprite(hue);
      ctx.drawImage(s, x - r, y - r, r * 2, r * 2);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    };

    const drawStar = (
      x: number, y: number, size: number, hue: number, alpha: number, hot: boolean,
    ) => {
      if (x < -40 || y < -40 || x > W + 40 || y > H + 40 || alpha <= 0.012) return;
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = alpha * 0.5;
      const s = sprite(hue);
      const hr = size * 5.5;
      ctx.drawImage(s, x - hr, y - hr, hr * 2, hr * 2);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = hot ? "#eafff6" : `hsl(${hue}, 55%, 80%)`;
      ctx.beginPath();
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.fill();
      if (hot) {
        ctx.globalCompositeOperation = "source-over";
        ctx.strokeStyle = "rgba(94, 234, 212, 0.95)";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(x, y, size + 3.5, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    };

    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const bg = ctx.createLinearGradient(0, 0, W, H);
      bg.addColorStop(0, "#0d1120");
      bg.addColorStop(0.55, "#090c15");
      bg.addColorStop(1, "#070910");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, W, H);

      const H2 = hlRef.current;
      const zoomRatio = cam.z / Math.max(uniFitZ, 1e-6);
      const sizeMul = clamp(Math.sqrt(zoomRatio), 0.8, 3.4);
      const now = performance.now();

      /* nebula glow behind everything (real galaxy centres) */
      for (const g of doc.galaxies) {
        const hot = H2.active && H2.gal.has(g.id);
        const base = H2.active ? (hot ? 0.55 : 0.05) : 0.17;
        const isHere = mode === "galaxy" && g.id === gid;
        drawGlow(g.hue, w2sx(g.cx), w2sy(g.cy), g.radius * cam.z * (isHere ? 2.6 : 3.4),
          isHere ? Math.max(base, 0.5) : base);
      }

      /* galaxy discs + labels */
      for (const g of doc.galaxies) {
        const cx = w2sx(g.cx);
        const cy = w2sy(g.cy);
        const R = g.radius * cam.z;
        if (cx < -R - 80 || cx > W + R + 80 || cy < -R - 80 || cy > H + R + 80) continue;
        const isHere = mode === "galaxy" && g.id === gid;
        const hot = H2.active && H2.gal.has(g.id);
        let a = H2.active ? (hot ? 1 : 0.15) : 0.85;
        if (mode === "galaxy" && !isHere) a *= 0.32;

        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(R, 4), 0, Math.PI * 2);
        ctx.strokeStyle = `hsla(${g.hue}, 65%, 68%, ${0.1 * a + (hot ? 0.45 : 0)})`;
        ctx.lineWidth = isHere ? 1.8 : 1;
        ctx.stroke();

        const fs = clamp(R * 0.2, 11, isHere ? 30 : 21);
        if (R > 13) {
          ctx.textAlign = "center";
          ctx.font = `700 ${fs}px ui-sans-serif, system-ui`;
          ctx.fillStyle = hot ? `hsla(${g.hue}, 75%, 84%, ${a})` : `rgba(233,236,220,${0.6 * a})`;
          ctx.fillText(g.label, cx, cy - R - fs * 0.5);
          ctx.font = `500 ${Math.max(10, fs * 0.6)}px ui-sans-serif, system-ui`;
          ctx.fillStyle = `rgba(176,181,160,${0.62 * a})`;
          ctx.fillText(`${g.count} segments`, cx, cy - R - fs * 0.5 + fs * 0.95);
        }

        if (isHere) {
          /* subclusters of the open galaxy */
          for (const s of g.subclusters) {
            const sx = w2sx(s.cx);
            const sy = w2sy(s.cy);
            const sr = s.radius * cam.z;
            const shot = H2.active && H2.sub.has(s.id);
            drawGlow(g.hue, sx, sy, sr * 1.7, shot ? 0.5 : 0.2);
            ctx.beginPath();
            ctx.arc(sx, sy, Math.max(sr, 3), 0, Math.PI * 2);
            ctx.strokeStyle = `hsla(${g.hue}, 60%, 75%, ${shot ? 0.55 : 0.18})`;
            ctx.stroke();
            if (sr > 24) {
              ctx.textAlign = "center";
              ctx.font = `600 ${clamp(sr * 0.13, 10, 14)}px ui-sans-serif, system-ui`;
              ctx.fillStyle = `rgba(222,226,208,${shot ? 0.95 : 0.7})`;
              ctx.fillText(s.label, sx, sy - 3);
              ctx.font = "500 10px ui-sans-serif, system-ui";
              ctx.fillStyle = "rgba(170,176,154,0.62)";
              ctx.fillText(`${s.count} clips`, sx, sy + 12);
            }
          }
        }
      }

      /* stars — real segment points */
      const gIdx = mode === "galaxy" && gid ? galIdxOf(gid) : -1;
      for (let gi = 0; gi < doc.galaxies.length; gi++) {
        const g = doc.galaxies[gi];
        const here = gIdx < 0 || gIdx === gi;
        const hotGal = H2.active && H2.gal.has(g.id);
        const pts = ptsByG[gi];
        if (!here) {
          /* other galaxies: faint dust for depth */
          for (const p of pts) {
            drawStar(w2sx(p.x), w2sy(p.y), 0.85, g.hue, 0.14, false);
          }
          continue;
        }
        for (let i = 0; i < pts.length; i++) {
          const p = pts[i];
          const hot = H2.active && H2.seg.has(p.id);
          let alpha = 0.45 + 0.55 * (1 - p.z);
          if (H2.active && !hotGal && !hot) alpha *= 0.13;
          const shimmer = 0.92 + 0.08 * Math.sin(now * 0.0012 + p.z * 9 + i * 0.37);
          const size = (1.1 + 1.7 * (1 - p.z)) * sizeMul * (hot ? 1.55 : 1) * shimmer;
          drawStar(w2sx(p.x), w2sy(p.y), Math.max(size, 0.7), g.hue, alpha, hot);
        }
      }

      /* hover + selection rings */
      if (hover) {
        ctx.strokeStyle = "rgba(255,255,255,0.85)";
        ctx.lineWidth = 1.2;
        if (hover.kind === "point") {
          ctx.beginPath();
          ctx.arc(w2sx(hover.p.x), w2sy(hover.p.y), 9, 0, Math.PI * 2);
          ctx.stroke();
        } else {
          const c = hover.kind === "galaxy" ? hover.g : hover.sub;
          ctx.beginPath();
          ctx.arc(w2sx(c.cx), w2sy(c.cy), Math.max(c.radius * cam.z, 8), 0, Math.PI * 2);
          ctx.stroke();
        }
      }
      if (selected) {
        ctx.strokeStyle = "#a8d188";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(w2sx(selected.x), w2sy(selected.y), 12, 0, Math.PI * 2);
        ctx.stroke();
      }
    };

    let last = performance.now();
    const frame = (t: number) => {
      if (disposed) return;
      const dt = Math.min((t - last) / 1000, 0.1);
      last = t;
      toCam(dt);
      draw();
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    setControls({
      reset: () => {
        if (mode === "galaxy") exitGalaxy();
        else fitUniverse();
      },
      zoomBy: (f) => zoomAt(f, W / 2, H / 2),
      back: exitGalaxy,
      enter: (id) => void enterGalaxy(id),
    });

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      cv.removeEventListener("pointerdown", onDown);
      cv.removeEventListener("pointermove", onMove);
      cv.removeEventListener("pointerup", onUp);
      cv.removeEventListener("pointerleave", onLeave);
      cv.removeEventListener("wheel", onWheel);
      cv.removeEventListener("dblclick", onDbl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc]);

  /* ---------------- render ---------------- */
  const topGalaxies = hl?.galaxies.slice(0, 6) ?? [];

  return (
    <div>
      <div className="eyebrow">EXPLORE GALAXY</div>
      <h1>
        Every idea, <span className="dim">a universe.</span>
      </h1>
      <p className="sub">
        Semantic galaxies discovered by clustering the real clip embeddings — click a galaxy to
        dive in, hover stars for thumbnails, click to preview.
      </p>

      <div className="row" style={{ marginBottom: 10 }}>
        <div className="search-shell" style={{ maxWidth: 460 }}>
          <span className="mag">⌕</span>
          <input
            type="text"
            placeholder="highlight the universe… e.g. dog playing"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runHighlight()}
          />
        </div>
        <button onClick={runHighlight} disabled={hlBusy || !q.trim()}>
          {hlBusy ? <span className="spinner" /> : "✦ Highlight"}
        </button>
        {hl && (
          <button className="ghost small" onClick={clearHighlight}>
            Clear search
          </button>
        )}
        <div style={{ flex: 1 }} />
        <button className="ghost small" onClick={() => controls?.back()} disabled={!crumb}>
          ← Back
        </button>
        <button className="ghost small" onClick={() => controls?.reset()}>
          ⟲ Reset view
        </button>
        <button className="ghost small" onClick={() => controls?.zoomBy(1.45)}>
          ＋
        </button>
        <button className="ghost small" onClick={() => controls?.zoomBy(1 / 1.45)}>
          －
        </button>
      </div>

      {hl && (
        <div className="hl-chips">
          <span className="muted small">Matching galaxies:</span>
          {topGalaxies.map((g) => {
            const meta = doc?.galaxies.find((x) => x.id === g.id);
            return (
              <button
                key={g.id}
                className="hl-chip"
                onClick={() => controls?.enter(g.id)}
                title={meta?.keywords.join(", ")}
              >
                {meta?.label ?? g.id}
                <span className="s">{g.score.toFixed(2)}</span>
              </button>
            );
          })}
          {hl.segments.length > 0 && (
            <span className="muted small">· {hl.segments.length} matching segments</span>
          )}
        </div>
      )}

      {err && <div className="error">{err}</div>}

      <div className="uni-wrap" ref={wrapRef} style={{ marginTop: 12 }}>
        <canvas ref={canvasRef} />

        <div className="uni-crumb">
          <span
            className="link"
            onClick={() => controls?.back()}
            style={{ opacity: crumb ? 1 : 0.55 }}
          >
            ◈ Universe
          </span>
          {crumb && (
            <>
              <span>›</span>
              <b>{crumb.label}</b>
              <span className="muted">· {crumb.count} segments</span>
            </>
          )}
        </div>

        {doc && (
          <div className="uni-status">
            {doc.count.toLocaleString()} segments · {doc.galaxy_count} galaxies ·{" "}
            {doc.subcluster_count} subclusters · silhouette {doc.silhouette} · k={doc.chosen_k} ·{" "}
            {doc.model.split("/")[1]} · cached {doc.created_at.slice(0, 10)} · double-click to zoom
            out
          </div>
        )}

        {!doc && !err && (
          <div className="uni-loading">
            <span className="spinner" style={{ width: 26, height: 26 }} />
            Building the universe from cached clusters…
          </div>
        )}

        {tooltip && (
          <div
            className="uni-tip"
            style={{
              left: Math.min(tooltip.sx + 16, window.innerWidth - 270),
              top: Math.min(tooltip.sy + 16, window.innerHeight - 280),
            }}
          >
            {tooltip.kind === "point" && (
              <>
                {tooltip.p.thumbnail_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={tooltip.p.thumbnail_url} alt="" />
                )}
                <div className="t-desc">{tooltip.p.description || tooltip.p.segment_id}</div>
                <div className="t-meta">
                  {tooltip.p.start_clock} → {tooltip.p.end_clock} · {tooltip.p.video_filename}
                </div>
                <div className="t-hint">Click to preview &amp; add to story</div>
              </>
            )}
            {tooltip.kind === "galaxy" && (
              <>
                <div className="t-desc">
                  {tooltip.g.label}
                  {tooltip.score !== undefined && (
                    <span style={{ color: "#bfe3a2" }}> · {tooltip.score.toFixed(2)}</span>
                  )}
                </div>
                <div className="t-meta">
                  {tooltip.g.count} segments · {tooltip.g.subclusters.length} subclusters
                </div>
                <div className="t-kw">
                  {tooltip.g.keywords.slice(0, 6).map((k) => (
                    <span key={k}>{k}</span>
                  ))}
                </div>
                <div className="t-hint">Click to enter the galaxy</div>
              </>
            )}
            {tooltip.kind === "sub" && (
              <>
                <div className="t-desc">
                  {tooltip.sub.label}
                  {tooltip.score !== undefined && (
                    <span style={{ color: "#bfe3a2" }}> · {tooltip.score.toFixed(2)}</span>
                  )}
                </div>
                <div className="t-meta">{tooltip.sub.count} segments</div>
                <div className="t-kw">
                  {tooltip.sub.keywords.slice(0, 5).map((k) => (
                    <span key={k}>{k}</span>
                  ))}
                </div>
                <div className="t-hint">Click to zoom into this subcluster</div>
              </>
            )}
          </div>
        )}

        {selected && (
          <div className="uni-selected">
            <button className="p-close" onClick={() => setSelected(null)}>
              ✕
            </button>
            {video ? (
              <video key={video} src={video} controls autoPlay />
            ) : (
              <div
                style={{
                  height: 176,
                  background: "#0e1119",
                  borderRadius: 9,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#8f93a3",
                }}
              >
                <span className="spinner" />
              </div>
            )}
            <div className="p-desc">{selected.description || selected.segment_id}</div>
            <div className="p-meta">
              {selected.start_clock} → {selected.end_clock} · {selected.video_filename}
            </div>
            <div className="p-actions">
              <button className="small" onClick={addToStory} disabled={adding}>
                {adding ? <span className="spinner" /> : addedMsg ?? "+ Add to Story"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
