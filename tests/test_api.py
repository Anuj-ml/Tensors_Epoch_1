"""E2E API tests. Requires the FastAPI server on API_BASE (default :8010)."""

import os
import time

import pytest
from httpx import Client

API = os.environ.get("REELMIND_API", "http://127.0.0.1:8010")


@pytest.fixture(scope="session")
def api() -> Client:
    try:
        r = Client(base_url=API, timeout=60.0)
        r.get("/api/health").raise_for_status()
        return r
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"API not reachable at {API}: {exc}")


def test_health(api: Client) -> None:
    h = api.get("/api/health").json()
    assert h["status"] == "ok"
    assert h["qdrant"]["reachable"] is True
    assert h["embedding"]["dim"] == 384
    assert h["qdrant"]["points"] > 0


def test_stats(api: Client) -> None:
    s = api.get("/api/stats").json()
    assert s["annotations"]["total"] > 100000
    assert s["segments"]["ready"] == 1970


def test_search_returns_grouped_results(api: Client) -> None:
    r = api.post("/api/search", json={"query": "someone driving a car", "top_k": 5})
    r.raise_for_status()
    body = r.json()
    assert body["result_count"] >= 1
    scores = [x["score"] for x in body["results"]]
    assert scores == sorted(scores, reverse=True)
    first = body["results"][0]
    assert first["segment_id"]
    assert first["start_clock"] and first["end_clock"]
    assert first["why_match"] or first["concepts"]


def test_search_no_keyword_matching(api: Client) -> None:
    r = api.post("/api/search", json={"query": "zzqx nonsense words", "top_k": 5})
    r.raise_for_status()
    body = r.json()
    assert body["query_type"] == "semantic"


def test_find_similar(api: Client) -> None:
    seed = api.post("/api/search", json={"query": "people cheering in a crowd", "top_k": 1})
    seg = seed.json()["results"][0]["segment_id"]
    r = api.post("/api/search/find-similar", json={"segment_id": seg, "top_k": 3})
    r.raise_for_status()
    body = r.json()
    assert body["result_count"] >= 1
    assert body["anchor"]["segment_id"] == seg


def test_segment_detail(api: Client) -> None:
    seed = api.post("/api/search", json={"query": "person walking outside", "top_k": 1})
    seg = seed.json()["results"][0]["segment_id"]
    r = api.get(f"/api/segments/{seg}")
    r.raise_for_status()
    d = r.json()
    assert d["duration_ms"] > 0
    assert d["annotation_count"] >= 1


def test_preview_transcode(api: Client) -> None:
    seed = api.post("/api/search", json={"query": "a dog running", "top_k": 1})
    seg = seed.json()["results"][0]["segment_id"]
    r = api.post(f"/api/segments/{seg}/preview")
    r.raise_for_status()
    url = r.json()["url"]
    assert url.startswith("/media/previews/")
    assert (os.path.join(os.path.dirname(os.path.dirname(__file__)), url.lstrip("/")))


def test_history_search_persisted(api: Client) -> None:
    api.post("/api/search", json={"query": "sunset over the ocean", "top_k": 3})
    h = api.get("/api/history/overview").json()
    assert any(s["query"] == "sunset over the ocean" for s in h["searches"])


def test_story_full_flow_and_roughcut(api: Client) -> None:
    seed = api.post("/api/search", json={"query": "person working on laptop", "top_k": 2})
    segs = [x["segment_id"] for x in seed.json()["results"]]
    assert len(segs) >= 2

    p = api.post("/api/story/projects", json={"name": "pytest story"})
    p.raise_for_status()
    pid = p.json()["project_id"]

    for seg in segs:
        c = api.post(f"/api/story/projects/{pid}/clips", json={"segment_id": seg})
        c.raise_for_status()

    proj = api.get(f"/api/story/projects/{pid}").json()
    assert len(proj["clips"]) == len(segs)

    clip0 = proj["clips"][0]
    up = api.patch(
        f"/api/story/clips/{clip0['story_clip_id']}",
        json={"source_start_ms": clip0["source_start_ms"] + 500},
    )
    up.raise_for_status()

    order = [c["story_clip_id"] for c in proj["clips"]][::-1]
    api.put(f"/api/story/projects/{pid}/order", json={"clip_ids": order}).raise_for_status()

    job = api.post(f"/api/story/projects/{pid}/roughcut", json={"force": True})
    job.raise_for_status()
    jid = job.json()["job_id"]
    for _ in range(60):
        st = api.get(f"/api/jobs/{jid}").json()
        if st["status"] in {"done", "failed", "cancelled"}:
            break
        time.sleep(2)
    assert st["status"] == "done", st.get("error")
    assert st["detail"]["output_url"]

    api.delete(f"/api/story/projects/{pid}").raise_for_status()


def test_script_analysis(api: Client) -> None:
    r = api.post(
        "/api/script/analyze",
        json={"script": "The team worked all night. Rain hit the city street.", "top_k": 3},
    )
    r.raise_for_status()
    a = r.json()
    assert a["beat_count"] >= 2
    assert a["beats"][0]["candidates"]


def test_explore_points(api: Client) -> None:
    e = api.get("/api/explore/points?refresh=false")
    e.raise_for_status()
    body = e.json()
    assert body["count"] == 1970
    assert body["bounds"]["max_x"] > body["bounds"]["min_x"]
