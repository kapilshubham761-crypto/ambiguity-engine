"""FastAPI bridge — exposes cognitive engine state to SAMO UI on port 8502."""
import sys, os, asyncio, json
from pathlib import Path

# Ensure src/ is importable
SRC = Path(__file__).parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="SAMO Cognitive Bridge", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/snapshot")
def snapshot():
    ms = _safe(lambda: __import__("meta_state").MetaState.get().snapshot(), {})
    en = _safe(lambda: __import__("energy").EnergyBudget.get().snapshot(), {})
    id_ = _safe(lambda: __import__("identity").IdentityTracker.get().snapshot(), {})
    sm = _safe(lambda: __import__("self_model").SelfModel.get().snapshot(), {})
    return {"meta_state": ms, "energy": en, "identity": id_, "self_model": sm}


@app.get("/api/memory")
def memory():
    tm  = _safe(lambda: __import__("memory").TemporalMemory.get(), None)
    ep  = _safe(lambda: __import__("episodes").EpisodeStore.get(), None)
    g   = _safe(lambda: __import__("graph").SemanticGraph(), None)

    working  = list(tm.working_set())[:40]  if tm  else []
    episodic = []
    if ep:
        for eps in list(ep.recent(20)):
            episodic.extend(eps.get("concepts", []))
        episodic = list(dict.fromkeys(episodic))[:60]

    semantic = []
    if g:
        try:
            nodes = sorted(g.G.nodes(data=True), key=lambda x: x[1].get("activation", 0), reverse=True)
            semantic = [n for n, _ in nodes[:80]]
        except Exception:
            pass

    return {"working": working, "episodic": episodic, "semantic": semantic}


@app.get("/api/worldview")
def worldview():
    return _safe(lambda: __import__("worldview").Worldview.get().snapshot(), {})


@app.get("/api/contradictions")
def contradictions():
    cr = _safe(lambda: __import__("contradiction").ContradictionRegistry.get(), None)
    if not cr:
        return {"unresolved": [], "all": []}
    all_c     = _safe(lambda: cr.all_contradictions(), [])
    unresolved = _safe(lambda: cr.unresolved(), [])
    return {"unresolved": unresolved, "all": all_c}


@app.get("/api/abstractions")
def abstractions():
    ab = _safe(lambda: __import__("abstractor").Abstractor.get(), None)
    if not ab:
        return {"abstractions": [], "hierarchy": {}}
    items = _safe(lambda: [
        {"concept": c, "confidence": d.get("confidence", 0), "connections": d.get("connections", 0)}
        for c, d in ab.abstractions.items()
    ], [])
    hier = _safe(lambda: ab.hierarchy, {})
    return {"abstractions": items, "hierarchy": hier}


@app.get("/api/graph")
def graph(max_nodes: int = 300):
    g = _safe(lambda: __import__("graph").SemanticGraph(), None)
    if not g:
        return {"nodes": [], "edges": []}
    try:
        nodes_data = sorted(g.G.nodes(data=True), key=lambda x: x[1].get("activation", 0), reverse=True)
        nodes = [
            {
                "id":          n,
                "activation":  float(d.get("activation",  0)),
                "tension":     float(d.get("tension",     0)),
                "novelty":     float(d.get("novelty",     0)),
                "connections": int(g.G.degree(n)),
            }
            for n, d in nodes_data[:max_nodes]
        ]
        node_set = {nd["id"] for nd in nodes}
        edges = [
            {"source": u, "target": v, "weight": float(d.get("weight", 0.5))}
            for u, v, d in g.G.edges(data=True)
            if u in node_set and v in node_set
        ][:500]
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}


@app.get("/api/reflection")
def reflection():
    return _safe(lambda: __import__("reflection").ReflectionMonitor.get().report(), {})


@app.get("/api/energy")
def energy():
    return _safe(lambda: __import__("energy").EnergyBudget.get().snapshot(), {})


@app.get("/api/identity")
def identity():
    return _safe(lambda: __import__("identity").IdentityTracker.get().snapshot(), {})


@app.get("/api/prediction-error")
def prediction_error():
    return _safe(lambda: __import__("prediction_error").PredictionError.get().snapshot(), {})


@app.get("/api/active-inference")
def active_inference():
    return _safe(lambda: __import__("active_inference").ActiveInference.get().seek_action(), {})


@app.get("/api/world-model")
def world_model():
    wm = _safe(lambda: __import__("world_model").WorldModel.get(), None)
    if not wm:
        return {"edges": []}
    edges = _safe(lambda: [
        {"source": s, "relation": r, "target": t, "strength": float(st)}
        for (s, r, t), st in sorted(wm.relations.items(), key=lambda x: -x[1])[:40]
    ], [])
    return {"edges": edges}


@app.get("/api/episodes")
def episodes(n: int = 50):
    ep = _safe(lambda: __import__("episodes").EpisodeStore.get(), None)
    if not ep:
        return {"episodes": []}
    return {"episodes": _safe(lambda: ep.recent(n), [])}


# ── WebSocket live stream ────────────────────────────────────────────────────

@app.websocket("/api/live")
async def live(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            ms  = _safe(lambda: __import__("meta_state").MetaState.get().snapshot(), {})
            en  = _safe(lambda: __import__("energy").EnergyBudget.get().snapshot(), {})
            cr  = _safe(lambda: __import__("contradiction").ContradictionRegistry.get(), None)
            ag  = _safe(lambda: __import__("agents").CognitiveAgents.get().dominant_agent(), "optimizer")
            gl  = _safe(lambda: __import__("goals").GoalEngine.get().current_goal(), "unknown")
            ai  = _safe(lambda: __import__("active_inference").ActiveInference.get().seek_action(), None)

            payload = {
                "mode":                ms.get("mode", "unknown"),
                "entropy":             float(ms.get("entropy", 0.5)),
                "energy":              float(en.get("total", 1.0) if isinstance(en, dict) else 1.0),
                "open_contradictions": int(_safe(lambda: len(cr.unresolved()), 0)) if cr else 0,
                "dominant_agent":      ag or "optimizer",
                "goal":                gl or "unknown",
                "ai_action":           ai,
                "ts":                  asyncio.get_event_loop().time(),
            }
            await ws.send_text(json.dumps(payload))
            await asyncio.sleep(1)
    except (WebSocketDisconnect, Exception):
        pass
