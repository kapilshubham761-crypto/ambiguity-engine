"""
Standalone multi-layer graph visualizer.
Run independently:  .venv\Scripts\streamlit run visualizer.py --server.port 8502
Reads data/ and graph.db directly. Not imported by or connected to engine code.
"""

import json
import sqlite3
import time
from pathlib import Path

import networkx as nx
import numpy as np
import plotly.graph_objects as go
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent
_DB   = _ROOT / "data" / "graph.db"
_DATA = _ROOT / "data"

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Visualizer",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
body, .stApp { background: #050508 !important; }
*, *::before, *::after { font-family: 'Consolas', 'Courier New', monospace !important; }
[data-testid="stSidebar"] { background: #07070e !important; border-right: 1px solid #141428 !important; }
[data-testid="stSidebarContent"] { background: #07070e !important; }
#MainMenu, footer, header[data-testid="stHeader"] { display: none !important; }
.block-container { padding: 0.5rem 1rem 2rem !important; max-width: 100% !important; }
label { color: #5050a0 !important; font-size: 11px !important; letter-spacing: 0.1em !important; text-transform: uppercase !important; }
[data-testid="stSelectbox"] > div > div { background: #0a0a18 !important; border-color: #1a1a30 !important; color: #9898c8 !important; }
[data-testid="stSlider"] > div > div { background: #1a1a30 !important; }
h1,h2,h3 { color: #6060a0 !important; font-size: 11px !important; letter-spacing: 0.2em !important;
           text-transform: uppercase !important; font-weight: 400 !important;
           border-bottom: 1px solid #141428 !important; padding-bottom: 4px !important; }
</style>
""", unsafe_allow_html=True)

# ── Categories ────────────────────────────────────────────────────────────────

CATEGORIES = [
    "Semantic Network",
    "Point Cloud 2D",
    "Point Cloud 3D",
    "Concept Activation",
    "Tension Map",
    "Memory Layers",
    "Causal Web",
    "Contradiction Map",
    "Transition Flow",
    "Bridge & Centrality",
    "Novelty Exposure",
    "Combined Overview",
]

COLOUR_MODES = ["activation", "tension", "novelty", "memory_depth", "degree"]

# ── Data loaders ──────────────────────────────────────────────────────────────

def _jload(name: str, default=None):
    try:
        return json.loads((_DATA / name).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default if default is not None else {}


@st.cache_data(ttl=12)
def load_graph_db(max_nodes: int):
    """Load nodes + edges from SQLite, limited to top max_nodes by activation."""
    try:
        con = sqlite3.connect(str(_DB), check_same_thread=False)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, text, embedding, activation_count FROM nodes "
            "ORDER BY activation_count DESC LIMIT ?", (max_nodes,)
        ).fetchall()
        node_ids = {r["id"] for r in rows}
        nodes = {
            r["id"]: {
                "text":  r["text"],
                "emb":   json.loads(r["embedding"]),
                "count": r["activation_count"],
            }
            for r in rows
        }
        edges = []
        for row in con.execute("SELECT source, target, weight FROM edges").fetchall():
            if row["source"] in node_ids and row["target"] in node_ids:
                edges.append((row["source"], row["target"], row["weight"]))
        con.close()
        return nodes, edges
    except Exception:
        return {}, []


@st.cache_data(ttl=12)
def load_all_data():
    meta    = _jload("meta_state.json")
    tension = _jload("tension.json")
    memory  = _jload("memory.json")
    wm      = _jload("world_model.json")
    contra  = _jload("contradictions.json", [])
    trans   = _jload("transitions.json")
    novelty = _jload("novelty.json")
    ident   = _jload("identity.json")
    energy  = _jload("energy.json")
    return meta, tension, memory, wm, contra, trans, novelty, ident, energy


def _pca(embeddings: list, n: int = 3) -> np.ndarray:
    """Numpy-only PCA — no sklearn needed."""
    X = np.array(embeddings, dtype=np.float32)
    X -= X.mean(axis=0)
    _, _, Vt = np.linalg.svd(X, full_matrices=False)
    return X @ Vt[:n].T


def _tension_score(concept: str, tension: dict) -> float:
    vals = tension.get(concept)
    if vals and isinstance(vals, list):
        return float(np.mean(vals[-20:]))
    return 0.0


def _activation(concept: str, meta: dict) -> float:
    entries = meta.get("entries", meta)
    v = entries.get(concept, 0)
    if isinstance(v, dict):
        return float(v.get("value", 0))
    return float(v) if v else 0.0


def _novelty_score(concept: str, novelty: dict) -> float:
    exp = novelty.get("exposures", novelty)
    e = exp.get(concept)
    if e and isinstance(e, dict):
        return float(e.get("times_seen", 0))
    return 0.0


# ── Dark plotly base ──────────────────────────────────────────────────────────

def _dark_layout(**kwargs):
    base = dict(
        paper_bgcolor="#050508",
        plot_bgcolor="#07070e",
        font=dict(family="Consolas", color="#6868a0", size=11),
        margin=dict(l=20, r=20, t=36, b=20),
        legend=dict(bgcolor="#09090f", bordercolor="#1a1a28", borderwidth=1, font=dict(size=10)),
        xaxis=dict(showgrid=True, gridcolor="#0f0f1e", zeroline=False, color="#404060"),
        yaxis=dict(showgrid=True, gridcolor="#0f0f1e", zeroline=False, color="#404060"),
    )
    base.update(kwargs)
    return go.Layout(**base)


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR CONTROLS
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="font-size:18px;letter-spacing:0.3em;color:#4a9eff;padding:12px 0 6px">
    VISUALIZER</div>
    <div style="font-size:10px;color:#303050;letter-spacing:0.08em;margin-bottom:16px">
    multi-layer engine explorer</div>
    """, unsafe_allow_html=True)

    category = st.selectbox("Layer", CATEGORIES, index=0)
    colour_by = st.selectbox("Colour by", COLOUR_MODES, index=0)
    max_nodes = st.slider("Max nodes", 50, 2000, 400, 50)
    edge_weight_min = st.slider("Min edge weight", 0.0, 2.0, 0.1, 0.05)

    st.markdown("---")
    _qp = st.query_params
    _default_refresh = _qp.get("refresh", "1") == "1"
    auto_refresh = st.toggle("Auto-refresh (12s)", value=_default_refresh)
    st.query_params["refresh"] = "1" if auto_refresh else "0"

    if st.button("Reset view", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown(f'<div style="font-size:10px;color:#252540">{time.strftime("%H:%M:%S")}</div>',
                unsafe_allow_html=True)

if auto_refresh:
    st.markdown('<meta http-equiv="refresh" content="12">', unsafe_allow_html=True)

# ── Load data ─────────────────────────────────────────────────────────────────

nodes, edges = load_graph_db(max_nodes)
meta, tension, memory, wm, contra, trans, novelty, ident, energy = load_all_data()

# ── Header ────────────────────────────────────────────────────────────────────

n_nodes = len(nodes)
n_edges = len(edges)
st.markdown(f"""
<div style="display:flex;align-items:center;gap:20px;padding:10px 4px 8px;
            border-bottom:1px solid #141428;margin-bottom:12px">
  <div style="font-size:18px;letter-spacing:0.25em;color:#b0b0d8">{category.upper()}</div>
  <div style="font-size:11px;color:#303050;margin-left:auto">
    {n_nodes:,} nodes · {n_edges:,} edges · top {max_nodes} by activation
  </div>
</div>
""", unsafe_allow_html=True)

if not nodes:
    st.markdown('<div style="color:#303050;padding:40px;text-align:center">No graph data yet — start the learner.</div>',
                unsafe_allow_html=True)
    st.stop()

# ── Build colour arrays ───────────────────────────────────────────────────────

texts  = [d["text"] for d in nodes.values()]
counts = [d["count"] for d in nodes.values()]

if colour_by == "activation":
    colour_vals = [_activation(t, meta) for t in texts]
    cscale, clabel = "Blues", "activation"
elif colour_by == "tension":
    colour_vals = [_tension_score(t, tension) for t in texts]
    cscale, clabel = "Reds", "tension"
elif colour_by == "novelty":
    colour_vals = [_novelty_score(t, novelty) for t in texts]
    cscale, clabel = "Greens", "times seen"
elif colour_by == "degree":
    G_tmp = nx.Graph()
    id_list = list(nodes.keys())
    G_tmp.add_nodes_from(id_list)
    G_tmp.add_edges_from([(u, v) for u, v, _ in edges])
    deg = dict(G_tmp.degree())
    colour_vals = [deg.get(nid, 0) for nid in nodes]
    cscale, clabel = "Plasma", "degree"
else:  # memory_depth
    mem_w = {k: v.get("value", 0) if isinstance(v, dict) else 0
             for k, v in memory.get("working", {}).items()}
    mem_e = {k: v.get("value", 0) if isinstance(v, dict) else 0
             for k, v in memory.get("episodic", {}).items()}
    mem_s = {k: v.get("value", 0) if isinstance(v, dict) else 0
             for k, v in memory.get("semantic", {}).items()}
    colour_vals = [mem_w.get(t, 0) + mem_e.get(t, 0) * 2 + mem_s.get(t, 0) * 3 for t in texts]
    cscale, clabel = "Purples", "memory depth"

# ═══════════════════════════════════════════════════════════════════════════════
# VIEWS
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. SEMANTIC NETWORK ───────────────────────────────────────────────────────

if category == "Semantic Network":
    G = nx.Graph()
    id_list = list(nodes.keys())
    G.add_nodes_from(id_list)
    for u, v, w in edges:
        if w >= edge_weight_min:
            G.add_edge(u, v, weight=w)

    # Spring layout on filtered graph
    with st.spinner("Computing layout…"):
        pos = nx.spring_layout(G, k=2 / max(1, n_nodes ** 0.5), iterations=40, seed=42)

    ex, ey, ew = [], [], []
    for u, v, w in edges:
        if G.has_edge(u, v):
            x0, y0 = pos[u]; x1, y1 = pos[v]
            ex += [x0, x1, None]; ey += [y0, y1, None]
            ew.append(w)

    nx_ = [pos[nid][0] for nid in id_list]
    ny_ = [pos[nid][1] for nid in id_list]

    fig = go.Figure(layout=_dark_layout(title="Semantic Co-occurrence Network"))
    fig.add_trace(go.Scatter(
        x=ex, y=ey, mode="lines",
        line=dict(color="#1a1a38", width=0.6), hoverinfo="none", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=nx_, y=ny_, mode="markers+text",
        text=[t[:18] for t in texts],
        textposition="top center",
        textfont=dict(size=8, color="#404060"),
        marker=dict(
            size=[max(4, min(20, c ** 0.4 * 3)) for c in counts],
            color=colour_vals,
            colorscale=cscale,
            colorbar=dict(title=clabel, thickness=10, len=0.5),
            line=dict(width=0.5, color="#1a1a30"),
        ),
        hovertemplate="<b>%{text}</b><br>" + clabel + ": %{marker.color:.3f}<extra></extra>",
        showlegend=False,
    ))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(showticklabels=False, showgrid=False)
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

# ── 2. POINT CLOUD 2D ─────────────────────────────────────────────────────────

elif category == "Point Cloud 2D":
    embs = [d["emb"] for d in nodes.values()]
    with st.spinner("PCA 2D…"):
        proj = _pca(embs, 2)

    fig = go.Figure(layout=_dark_layout(
        title="Embedding Space — PCA 2D",
        xaxis_title="PC1", yaxis_title="PC2",
    ))
    fig.add_trace(go.Scatter(
        x=proj[:, 0], y=proj[:, 1],
        mode="markers",
        text=texts,
        marker=dict(
            size=[max(3, min(14, c ** 0.35 * 2.5)) for c in counts],
            color=colour_vals, colorscale=cscale,
            colorbar=dict(title=clabel, thickness=10, len=0.6),
            opacity=0.85,
            line=dict(width=0.3, color="#0a0a18"),
        ),
        hovertemplate="<b>%{text}</b><br>" + clabel + ": %{marker.color:.3f}<extra></extra>",
    ))
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

# ── 3. POINT CLOUD 3D ─────────────────────────────────────────────────────────

elif category == "Point Cloud 3D":
    embs = [d["emb"] for d in nodes.values()]
    with st.spinner("PCA 3D…"):
        proj = _pca(embs, 3)

    fig = go.Figure(layout=go.Layout(
        paper_bgcolor="#050508", plot_bgcolor="#07070e",
        font=dict(family="Consolas", color="#6868a0", size=11),
        margin=dict(l=0, r=0, t=36, b=0),
        title=dict(text="Embedding Space — PCA 3D", font=dict(color="#6868a0", size=12)),
        scene=dict(
            bgcolor="#070710",
            xaxis=dict(showgrid=True, gridcolor="#0f0f20", zeroline=False, color="#303050"),
            yaxis=dict(showgrid=True, gridcolor="#0f0f20", zeroline=False, color="#303050"),
            zaxis=dict(showgrid=True, gridcolor="#0f0f20", zeroline=False, color="#303050"),
        ),
    ))
    fig.add_trace(go.Scatter3d(
        x=proj[:, 0], y=proj[:, 1], z=proj[:, 2],
        mode="markers",
        text=texts,
        marker=dict(
            size=[max(2, min(10, c ** 0.35 * 2)) for c in counts],
            color=colour_vals, colorscale=cscale,
            colorbar=dict(title=clabel, thickness=10, len=0.5),
            opacity=0.85,
            line=dict(width=0),
        ),
        hovertemplate="<b>%{text}</b><br>" + clabel + ": %{marker.color:.3f}<extra></extra>",
    ))
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

# ── 4. CONCEPT ACTIVATION ─────────────────────────────────────────────────────

elif category == "Concept Activation":
    entries = meta.get("entries", meta)
    act = {}
    for k, v in entries.items():
        val = v.get("value", 0) if isinstance(v, dict) else float(v or 0)
        if val > 0:
            act[k] = val
    top = sorted(act.items(), key=lambda x: -x[1])[:80]
    if not top:
        st.info("No active concepts yet.")
    else:
        labels, vals = zip(*top)
        colours = ["#ef4444" if v > 0.6 else "#f59e0b" if v > 0.3 else "#4a9eff" for v in vals]
        fig = go.Figure(layout=_dark_layout(
            title="Top Concept Activations",
            xaxis_title="activation", yaxis_title="",
        ))
        fig.add_trace(go.Bar(
            x=list(vals), y=list(labels),
            orientation="h",
            marker=dict(color=colours, line=dict(width=0)),
            hovertemplate="<b>%{y}</b><br>activation: %{x:.4f}<extra></extra>",
        ))
        fig.update_layout(height=max(400, len(top) * 18), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)

# ── 5. TENSION MAP ────────────────────────────────────────────────────────────

elif category == "Tension Map":
    ten_scores = {k: float(np.mean(v[-20:])) for k, v in tension.items()
                  if isinstance(v, list) and v}
    top_t = sorted(ten_scores.items(), key=lambda x: -x[1])[:100]
    if not top_t:
        st.info("No tension data yet.")
    else:
        # Find these concepts in node graph for position
        text_to_nid = {d["text"]: nid for nid, d in nodes.items()}
        t_labels = [k for k, _ in top_t]
        t_vals   = [v for _, v in top_t]

        fig = go.Figure(layout=_dark_layout(
            title="Concept Tension — mean ambiguity pressure",
            xaxis_title="tension score", yaxis_title="",
        ))
        colours_t = ["#ef4444" if v > 0.6 else "#f59e0b" if v > 0.4 else "#8b5cf6"
                     for v in t_vals]
        fig.add_trace(go.Bar(
            x=t_vals, y=t_labels, orientation="h",
            marker=dict(color=colours_t, line=dict(width=0)),
            hovertemplate="<b>%{y}</b><br>tension: %{x:.3f}<extra></extra>",
        ))
        fig.update_layout(height=max(400, len(top_t) * 16), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)

# ── 6. MEMORY LAYERS ──────────────────────────────────────────────────────────

elif category == "Memory Layers":
    layer_data = []
    for layer, col, depth in [("working", "#4a9eff", 3), ("episodic", "#8b5cf6", 2), ("semantic", "#44ff88", 1)]:
        items = memory.get(layer, {})
        for concept, v in items.items():
            val = v.get("value", 0) if isinstance(v, dict) else float(v or 0)
            if val > 0.01:
                layer_data.append({"concept": concept, "layer": layer, "value": val,
                                   "col": col, "depth": depth})
    if not layer_data:
        st.info("No memory data yet.")
    else:
        by_layer = {"working": [], "episodic": [], "semantic": []}
        for d in layer_data:
            by_layer[d["layer"]].append(d)

        fig = go.Figure(layout=_dark_layout(title="Temporal Memory Layers"))
        for layer, col in [("working", "#4a9eff"), ("episodic", "#8b5cf6"), ("semantic", "#44ff88")]:
            items = sorted(by_layer[layer], key=lambda x: -x["value"])[:50]
            if items:
                fig.add_trace(go.Bar(
                    name=layer,
                    x=[d["value"] for d in items],
                    y=[d["concept"][:24] for d in items],
                    orientation="h",
                    marker=dict(color=col, opacity=0.8, line=dict(width=0)),
                    hovertemplate="<b>%{y}</b><br>value: %{x:.4f}<extra></extra>",
                ))
        fig.update_layout(
            barmode="group",
            height=max(500, min(len(layer_data), 80) * 14),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True)

# ── 7. CAUSAL WEB ─────────────────────────────────────────────────────────────

elif category == "Causal Web":
    wm_edges = wm.get("edges", [])
    if not wm_edges:
        st.info("No causal edges yet — world model builds over time.")
    else:
        G_wm = nx.DiGraph()
        for e in wm_edges:
            G_wm.add_edge(e["source"], e["target"],
                          relation=e.get("relation", ""),
                          confidence=e.get("confidence", 0.5))

        with st.spinner("Layout…"):
            pos = nx.spring_layout(G_wm, k=3 / max(1, len(G_wm) ** 0.5), iterations=50, seed=7)

        RELATION_COL = {
            "causes": "#ef4444", "predicts": "#4a9eff",
            "suppresses": "#f59e0b", "depends_on": "#44ff88",
        }
        # Group edges by relation
        rel_groups: dict[str, list] = {}
        for u, v, d in G_wm.edges(data=True):
            rel = d.get("relation", "other")
            rel_groups.setdefault(rel, []).append((u, v, d.get("confidence", 0.5)))

        fig = go.Figure(layout=_dark_layout(title="Causal Web — World Model"))
        for rel, grp in rel_groups.items():
            col = RELATION_COL.get(rel, "#6868a0")
            ex, ey = [], []
            for u, v, conf in grp:
                if u in pos and v in pos:
                    x0, y0 = pos[u]; x1, y1 = pos[v]
                    ex += [x0, x1, None]; ey += [y0, y1, None]
            if ex:
                fig.add_trace(go.Scatter(
                    x=ex, y=ey, mode="lines", name=rel,
                    line=dict(color=col, width=1.5),
                    hoverinfo="name",
                ))

        nx_ = [pos[n][0] for n in G_wm.nodes()]
        ny_ = [pos[n][1] for n in G_wm.nodes()]
        fig.add_trace(go.Scatter(
            x=nx_, y=ny_, mode="markers+text",
            text=[n[:16] for n in G_wm.nodes()],
            textposition="top center",
            textfont=dict(size=8, color="#404060"),
            marker=dict(size=8, color="#4a9eff", opacity=0.8),
            hovertext=list(G_wm.nodes()),
            hovertemplate="<b>%{hovertext}</b><extra></extra>",
            showlegend=False,
        ))
        fig.update_xaxes(showticklabels=False, showgrid=False)
        fig.update_yaxes(showticklabels=False, showgrid=False)
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

# ── 8. CONTRADICTION MAP ──────────────────────────────────────────────────────

elif category == "Contradiction Map":
    if not contra or not isinstance(contra, list):
        st.info("No contradictions registered yet.")
    else:
        open_c  = [c for c in contra if c.get("resolution_status") == "open"]
        closed_c = [c for c in contra if c.get("resolution_status") != "open"]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f'<div style="font-size:11px;color:#ef4444;letter-spacing:0.1em">'
                        f'OPEN CONTRADICTIONS: {len(open_c)}</div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div style="font-size:11px;color:#44ff88;letter-spacing:0.1em">'
                        f'RESOLVED: {len(closed_c)}</div>', unsafe_allow_html=True)

        # Scatter: x=tension_a, y=tension_b, size=confidence_gap, colour=open/closed
        if open_c:
            ta = [c.get("tension_a", 0) for c in open_c]
            tb = [c.get("tension_b", 0) for c in open_c]
            cg = [c.get("confidence_gap", 0.1) for c in open_c]
            labels = [f"{c.get('concept_a','')} ↔ {c.get('concept_b','')}" for c in open_c]
            ctypes = [c.get("conflict_type", "unknown") for c in open_c]

            fig = go.Figure(layout=_dark_layout(
                title="Open Contradictions",
                xaxis_title="tension_a", yaxis_title="tension_b",
            ))
            fig.add_trace(go.Scatter(
                x=ta, y=tb, mode="markers",
                text=labels,
                marker=dict(
                    size=[max(6, min(30, g * 40)) for g in cg],
                    color=cg, colorscale="Reds",
                    colorbar=dict(title="confidence gap", thickness=10),
                    opacity=0.85, line=dict(width=0.5, color="#300000"),
                ),
                hovertemplate="<b>%{text}</b><br>tension A: %{x:.2f}<br>tension B: %{y:.2f}<extra></extra>",
            ))
            st.plotly_chart(fig, use_container_width=True)

        with st.expander(f"All contradictions ({len(contra)})"):
            for c in contra[:50]:
                status_col = "#ef4444" if c.get("resolution_status") == "open" else "#44ff88"
                st.markdown(
                    f'<div style="font-size:12px;padding:3px 0;color:#7878a8">'
                    f'<span style="color:{status_col}">●</span>&nbsp;'
                    f'<b style="color:#9898c8">{c.get("concept_a","")}</b>'
                    f' ↔ <b style="color:#9898c8">{c.get("concept_b","")}</b>'
                    f'&nbsp;<span style="color:#404060">({c.get("conflict_type","?")})'
                    f'&nbsp;gap={c.get("confidence_gap",0):.2f}</span></div>',
                    unsafe_allow_html=True,
                )

# ── 9. TRANSITION FLOW ────────────────────────────────────────────────────────

elif category == "Transition Flow":
    SEP = "__SEP__"
    t_edges = [(k.split(SEP)[0], k.split(SEP)[1], v)
               for k, v in trans.items()
               if SEP in k and isinstance(v, (int, float))]
    t_edges = sorted(t_edges, key=lambda x: -x[2])[:200]

    if not t_edges:
        st.info("No transition data yet.")
    else:
        G_t = nx.DiGraph()
        for a, b, w in t_edges:
            G_t.add_edge(a, b, weight=w)

        with st.spinner("Layout…"):
            pos = nx.spring_layout(G_t, k=2 / max(1, len(G_t) ** 0.5), iterations=40, seed=3)

        max_w = max(w for _, _, w in t_edges) or 1
        ex, ey, ew = [], [], []
        for a, b, w in t_edges:
            if a in pos and b in pos:
                x0, y0 = pos[a]; x1, y1 = pos[b]
                ex += [x0, x1, None]; ey += [y0, y1, None]
                ew.append(w)

        fig = go.Figure(layout=_dark_layout(title="Concept Transition Flow"))
        fig.add_trace(go.Scatter(
            x=ex, y=ey, mode="lines",
            line=dict(color="#1a2a4a", width=0.8),
            hoverinfo="none", showlegend=False,
        ))

        nx_ = [pos[n][0] for n in G_t.nodes()]
        ny_ = [pos[n][1] for n in G_t.nodes()]
        deg = dict(G_t.out_degree())
        fig.add_trace(go.Scatter(
            x=nx_, y=ny_, mode="markers+text",
            text=[n[:16] for n in G_t.nodes()],
            textposition="top center",
            textfont=dict(size=8, color="#404060"),
            marker=dict(
                size=[max(5, min(20, deg.get(n, 1) * 3)) for n in G_t.nodes()],
                color=[deg.get(n, 0) for n in G_t.nodes()],
                colorscale="Blues",
                colorbar=dict(title="out-degree", thickness=10, len=0.5),
            ),
            hovertemplate="<b>%{text}</b><extra></extra>",
            showlegend=False,
        ))
        fig.update_xaxes(showticklabels=False, showgrid=False)
        fig.update_yaxes(showticklabels=False, showgrid=False)
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

# ── 10. BRIDGE & CENTRALITY ───────────────────────────────────────────────────

elif category == "Bridge & Centrality":
    G_b = nx.Graph()
    id_list = list(nodes.keys())
    G_b.add_nodes_from(id_list)
    for u, v, w in edges:
        if w >= edge_weight_min:
            G_b.add_edge(u, v, weight=w)

    with st.spinner("Computing centrality…"):
        if len(G_b) > 500:
            sample = list(G_b.nodes())[:500]
            G_sub  = G_b.subgraph(sample)
            bc = nx.betweenness_centrality(G_sub, normalized=True, weight="weight")
        else:
            bc = nx.betweenness_centrality(G_b, normalized=True, weight="weight")

    top_bc = sorted(bc.items(), key=lambda x: -x[1])[:80]
    if not top_bc:
        st.info("No bridge data.")
    else:
        bc_labels = [nodes[nid]["text"][:24] for nid, _ in top_bc]
        bc_vals   = [v for _, v in top_bc]
        colours_bc = ["#ef4444" if v > 0.01 else "#f59e0b" if v > 0.005 else "#4a9eff"
                      for v in bc_vals]

        fig = go.Figure(layout=_dark_layout(
            title="Bridge Nodes — Betweenness Centrality",
            xaxis_title="betweenness centrality",
        ))
        fig.add_trace(go.Bar(
            x=bc_vals, y=bc_labels, orientation="h",
            marker=dict(color=colours_bc, line=dict(width=0)),
            hovertemplate="<b>%{y}</b><br>centrality: %{x:.5f}<extra></extra>",
        ))
        fig.update_layout(height=max(400, len(top_bc) * 16), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)

# ── 11. NOVELTY EXPOSURE ──────────────────────────────────────────────────────

elif category == "Novelty Exposure":
    exp = novelty.get("exposures", novelty)
    nov_data = []
    for concept, v in exp.items():
        if isinstance(v, dict):
            nov_data.append((concept, v.get("times_seen", 0)))
    nov_data.sort(key=lambda x: -x[1])
    nov_data = nov_data[:100]

    if not nov_data:
        st.info("No novelty data yet.")
    else:
        nov_labels = [k[:28] for k, _ in nov_data]
        nov_vals   = [v for _, v in nov_data]
        max_v      = max(nov_vals) or 1
        colours_n  = [f"rgba({int(50 + v/max_v*200)}, {int(50 + (1-v/max_v)*180)}, 180, 0.85)"
                      for v in nov_vals]

        fig = go.Figure(layout=_dark_layout(
            title="Novelty — Exposure Count (high = seen many times = not novel)",
            xaxis_title="times seen",
        ))
        fig.add_trace(go.Bar(
            x=nov_vals, y=nov_labels, orientation="h",
            marker=dict(color=colours_n, line=dict(width=0)),
            hovertemplate="<b>%{y}</b><br>times seen: %{x}<extra></extra>",
        ))
        fig.update_layout(height=max(400, len(nov_data) * 14), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)

# ── 12. COMBINED OVERVIEW ─────────────────────────────────────────────────────

elif category == "Combined Overview":
    col_a, col_b = st.columns(2)

    # Top activated concepts
    with col_a:
        st.markdown("### Concept Activation")
        entries = meta.get("entries", meta)
        act_top = sorted(
            [(k, v.get("value", 0) if isinstance(v, dict) else float(v or 0))
             for k, v in entries.items() if (v.get("value", 0) if isinstance(v, dict) else float(v or 0)) > 0],
            key=lambda x: -x[1],
        )[:30]
        if act_top:
            fig_a = go.Figure(layout=_dark_layout())
            fig_a.add_trace(go.Bar(
                x=[v for _, v in act_top], y=[k[:20] for k, _ in act_top],
                orientation="h",
                marker=dict(color="#4a9eff", opacity=0.8, line=dict(width=0)),
            ))
            fig_a.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10),
                                 yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_a, use_container_width=True)

    # Top tension concepts
    with col_b:
        st.markdown("### Tension Scores")
        ten_top = sorted(
            {k: float(np.mean(v[-20:])) for k, v in tension.items()
             if isinstance(v, list) and v}.items(),
            key=lambda x: -x[1],
        )[:30]
        if ten_top:
            fig_t = go.Figure(layout=_dark_layout())
            fig_t.add_trace(go.Bar(
                x=[v for _, v in ten_top], y=[k[:20] for k, _ in ten_top],
                orientation="h",
                marker=dict(color="#ef4444", opacity=0.8, line=dict(width=0)),
            ))
            fig_t.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10),
                                 yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_t, use_container_width=True)

    col_c, col_d = st.columns(2)

    # Memory layer summary
    with col_c:
        st.markdown("### Memory Layer Sizes")
        layer_counts = {
            layer: len([v for v in memory.get(layer, {}).values()
                        if (v.get("value", 0) if isinstance(v, dict) else float(v or 0)) > 0.01])
            for layer in ["working", "episodic", "semantic"]
        }
        fig_m = go.Figure(layout=_dark_layout())
        fig_m.add_trace(go.Bar(
            x=list(layer_counts.keys()), y=list(layer_counts.values()),
            marker=dict(color=["#4a9eff", "#8b5cf6", "#44ff88"], line=dict(width=0)),
        ))
        fig_m.update_layout(height=250, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_m, use_container_width=True)

    # World model edge types
    with col_d:
        st.markdown("### Causal Relation Types")
        wm_edges = wm.get("edges", [])
        rel_counts: dict[str, int] = {}
        for e in wm_edges:
            rel = e.get("relation", "unknown")
            rel_counts[rel] = rel_counts.get(rel, 0) + 1
        if rel_counts:
            RCOLS = {"causes": "#ef4444", "predicts": "#4a9eff",
                     "suppresses": "#f59e0b", "depends_on": "#44ff88"}
            fig_r = go.Figure(layout=_dark_layout())
            fig_r.add_trace(go.Bar(
                x=list(rel_counts.keys()), y=list(rel_counts.values()),
                marker=dict(color=[RCOLS.get(r, "#6060a0") for r in rel_counts],
                            line=dict(width=0)),
            ))
            fig_r.update_layout(height=250, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_r, use_container_width=True)

# ── Footer stats ──────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="display:flex;gap:24px;padding:10px 4px;border-top:1px solid #101020;
            font-size:11px;color:#252540;margin-top:8px;flex-wrap:wrap">
  <span>nodes <b style="color:#3a3a60">{n_nodes:,}</b></span>
  <span>edges <b style="color:#3a3a60">{n_edges:,}</b></span>
  <span>tensions <b style="color:#3a3a60">{len(tension):,}</b></span>
  <span>contradictions <b style="color:#3a3a60">{len(contra) if isinstance(contra,list) else 0}</b></span>
  <span>causal edges <b style="color:#3a3a60">{len(wm.get('edges',[]))}</b></span>
  <span>transitions <b style="color:#3a3a60">{len(trans):,}</b></span>
  <span>memory items <b style="color:#3a3a60">{sum(len(memory.get(l,{})) for l in ['working','episodic','semantic']):,}</b></span>
  <span style="margin-left:auto">refreshes every 12s · {time.strftime('%H:%M:%S')}</span>
</div>
""", unsafe_allow_html=True)
