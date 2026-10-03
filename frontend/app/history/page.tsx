"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  SearchHistoryItem,
  historyOverview,
  restoreSearch,
  saveSearch,
} from "@/lib/api";

export default function HistoryPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof historyOverview>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const router = useRouter();

  const load = useCallback(async () => {
    try {
      setData(await historyOverview());
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const restore = async (id: string) => {
    try {
      const r = await restoreSearch(id);
      sessionStorage.setItem("reelmind.lastSearch", JSON.stringify(r));
      router.push(`/?q=${encodeURIComponent(r.query)}`);
    } catch (e) {
      setErr(String(e));
    }
  };

  const save = async (id: string) => {
    try {
      await saveSearch(id);
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const Item = ({ item }: { item: SearchHistoryItem }) => (
    <div className="list-item">
      <div>
        <b>{item.query}</b>
        <div className="meta small">
          {item.query_type} · {item.result_count} results ·{" "}
          {new Date(item.created_at).toLocaleString()}
          {item.saved_label ? ` · ★ ${item.saved_label}` : ""}
        </div>
      </div>
      <div className="row">
        <button className="small ghost" onClick={() => restore(item.search_id)}>
          Restore
        </button>
        {!item.is_saved && (
          <button className="small" onClick={() => save(item.search_id)}>
            ★ Save
          </button>
        )}
      </div>
    </div>
  );

  return (
    <div>
      <div className="eyebrow">SEARCH HISTORY</div>
      <h1>
        Everything you’ve <span className="dim">discovered.</span>
      </h1>
      <p className="sub">Every search, saved queries, story activity — stored locally in SQLite.</p>
      {err && <div className="error">{err}</div>}

      <div className="stat-grid" style={{ marginBottom: 20 }}>
        {data &&
          Object.entries(data.event_counts).map(([k, v]) => (
            <div className="stat" key={k}>
              <div className="v">{v}</div>
              <div className="k">{k}</div>
            </div>
          ))}
        <div className="stat">
          <div className="v">{data?.searches.length ?? 0}</div>
          <div className="k">total searches</div>
        </div>
        <div className="stat">
          <div className="v">{data?.recent_clips.length ?? 0}</div>
          <div className="k">clip adds</div>
        </div>
      </div>

      <h2>Saved searches</h2>
      {data?.saved_searches.length ? (
        data.saved_searches.map((s) => <Item key={s.search_id} item={s} />)
      ) : (
        <div className="muted small">None yet — save one from the AI Brain page.</div>
      )}

      <h2>Recent searches</h2>
      {data?.searches.length ? (
        data.searches.map((s) => <Item key={s.search_id} item={s} />)
      ) : (
        <div className="muted small">No searches yet.</div>
      )}
    </div>
  );
}
