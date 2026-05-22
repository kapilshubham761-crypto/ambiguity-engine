"""7.3 — Graph view: 2D / 3D force-directed network with left/right brain filter."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import numpy as np

st.title("🕸️ Graph")
st.caption("Force-directed view of the semantic graph. Node size = activation count. Edge opacity = weight.")

# ------------------------------------------------------------------ #
# Left / Right brain classification                                    #
# ------------------------------------------------------------------ #
_LEFT_KEYWORDS = {
    'logic', 'logic', 'analysis', 'analytic', 'algorithm', 'data', 'math',
    'number', 'equation', 'theory', 'system', 'structure', 'function',
    'sequence', 'pattern', 'model', 'code', 'language', 'syntax', 'grammar',
    'probability', 'statistic', 'linear', 'reason', 'cause', 'law', 'rule',
    'definition', 'proof', 'fact', 'evidence', 'science', 'physics',
    'chemistry', 'biology', 'engineering', 'computation', 'network',
    'classification', 'category', 'hierarchy', 'order', 'formal', 'symbol',
    'calculation', 'formula', 'variable', 'constant', 'theorem', 'axiom',
    'inference', 'deduction', 'induction', 'prediction', 'measurement',
    'experiment', 'hypothesis', 'objective', 'rational', 'logical',
}

_RIGHT_KEYWORDS = {
    'emotion', 'feeling', 'art', 'music', 'dream', 'imagination', 'creative',
    'intuition', 'metaphor', 'story', 'narrative', 'image', 'colour', 'colour',
    'beauty', 'aesthetic', 'poetry', 'poem', 'rhythm', 'melody', 'harmony',
    'symbol', 'myth', 'legend', 'spiritual', 'consciousness', 'awareness',
    'empathy', 'compassion', 'joy', 'fear', 'love', 'grief', 'wonder',
    'fantasy', 'vision', 'sense', 'perception', 'body', 'gesture', 'dance',
    'play', 'humour', 'irony', 'paradox', 'ambiguity', 'mystery', 'holistic',
    'spontaneous', 'random', 'chaos', 'flow', 'subconscious', 'dream',
    'identity', 'self', 'soul', 'meaning', 'purpose', 'experience', 'qualia',
}

def _brain_side(text: str) -> str:
    """Return 'left', 'right', or 'both' based on keyword overlap."""
    words = set(text.lower().replace('-', ' ').split())
    is_left  = bool(words & _LEFT_KEYWORDS)
    is_right = bool(words & _RIGHT_KEYWORDS)
    if is_left and is_right:
        return 'both'
    if is_left:
        return 'left'
    if is_right:
        return 'right'
    return 'both'   # unclassified goes to both

from graph import SemanticGraph

@st.cache_resource(show_spinner="Loading graph…")
def load_graph():
    return SemanticGraph()

g = load_graph()

if g.node_count == 0:
    st.info("Graph is empty — run some inputs through the Runner page first.")
    st.stop()

# ------------------------------------------------------------------ #
# Controls                                                             #
# ------------------------------------------------------------------ #
ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([2, 2, 2, 1, 1])
_n         = max(g.node_count, 100)
min_weight = ctrl1.slider("Min edge weight", 0.0, 1.0, 0.05, 0.01)
max_nodes  = ctrl2.slider("Max nodes", 10, max(1000, _n), max(10, min(1000, _n)))
brain_mode = ctrl3.radio("Brain", ["🧠 Both", "◀️ Left", "▶️ Right"],
                         horizontal=True, label_visibility='collapsed')

# 3-D toggle stored in session state
if 'graph_3d' not in st.session_state:
    st.session_state.graph_3d = False

with ctrl4:
    st.write("")
    if st.button("🧊 3D" if not st.session_state.graph_3d else "⬛ 2D",
                 use_container_width=True,
                 help="Toggle between 2D and 3D view"):
        st.session_state.graph_3d = not st.session_state.graph_3d
        st.rerun()

with ctrl5:
    st.write("")
    if st.button("↺ Reset", use_container_width=True,
                 help="Return to default 2D view"):
        st.session_state.graph_3d = False
        st.rerun()

mode_3d = st.session_state.graph_3d

# ------------------------------------------------------------------ #
# Subgraph + brain filter                                              #
# ------------------------------------------------------------------ #
_brain_filter = {'🧠 Both': 'both', '◀️ Left': 'left', '▶️ Right': 'right'}[brain_mode]

top_nodes = sorted(
    g._g.nodes(data=True),
    key=lambda x: x[1]['activation_count'],
    reverse=True,
)

# Apply brain filter
if _brain_filter != 'both':
    top_nodes = [
        (nid, data) for nid, data in top_nodes
        if _brain_side(data['text']) in (_brain_filter, 'both')
    ]

top_nodes = top_nodes[:max_nodes]
sub_ids   = {nid for nid, _ in top_nodes}

H = g._g.subgraph(sub_ids).copy()
H.remove_edges_from([(u, v) for u, v, d in H.edges(data=True) if d['weight'] < min_weight])

_BRAIN_SIDE_MAP = {nid: _brain_side(data['text']) for nid, data in H.nodes(data=True)}

if H.number_of_nodes() == 0:
    st.warning("No nodes to display with current filters.")
    st.stop()

# ------------------------------------------------------------------ #
# Layout — 2D or 3D positions                                          #
# ------------------------------------------------------------------ #
n = H.number_of_nodes()

if mode_3d:
    if n > 300:
        rng = np.random.default_rng(42)
        nodes_list = list(H.nodes())
        coords = rng.uniform(-1, 1, (n, 3))
        pos3 = {nid: coords[i] for i, nid in enumerate(nodes_list)}
    else:
        pos2 = nx.spring_layout(H, seed=42, k=1.2)
        rng  = np.random.default_rng(42)
        pos3 = {nid: np.append(xy, rng.uniform(-1, 1)) for nid, xy in pos2.items()}
else:
    if n > 300:
        pos2 = nx.random_layout(H, seed=42)
    else:
        pos2 = nx.spring_layout(H, seed=42, k=1.2)

# ------------------------------------------------------------------ #
# Common: node colours / sizes                                         #
# ------------------------------------------------------------------ #
acts    = [data['activation_count'] for _, data in H.nodes(data=True)]
act_max = max(acts) if acts else 1
weights = [d['weight'] for _, _, d in H.edges(data=True)]
w_max   = max(weights) if weights else 1.0

node_ids   = list(H.nodes())
node_acts  = [H.nodes[nid]['activation_count'] for nid in node_ids]
node_sizes = [8 + 18 * (a / act_max) for a in node_acts]

_SIDE_COLOUR = {'left': '#4a9eff', 'right': '#ff6b6b', 'both': '#50c8a8'}
node_colours = [_SIDE_COLOUR[_BRAIN_SIDE_MAP.get(nid, 'both')] for nid in node_ids]

node_hover = [
    f"<b>{H.nodes[nid]['text']}</b><br>"
    f"side={_BRAIN_SIDE_MAP.get(nid,'both')}  "
    f"activations={H.nodes[nid]['activation_count']}  degree={H.degree(nid)}<br>"
    f"first={H.nodes[nid]['first_seen'][:10]}  last={H.nodes[nid]['last_seen'][:10]}"
    for nid in node_ids
]
node_labels = [H.nodes[nid]['text'] for nid in node_ids]

# ------------------------------------------------------------------ #
# Build figure                                                         #
# ------------------------------------------------------------------ #
if mode_3d:
    # --- 3D edges ---
    edge_x, edge_y, edge_z = [], [], []
    for u, v, data in H.edges(data=True):
        x0, y0, z0 = pos3[u]
        x1, y1, z1 = pos3[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        edge_z += [z0, z1, None]

    edge_trace = go.Scatter3d(
        x=edge_x, y=edge_y, z=edge_z,
        mode='lines',
        line=dict(width=1, color='rgba(120,120,140,0.35)'),
        hoverinfo='none',
        showlegend=False,
    )

    # --- 3D nodes ---
    nx_ = [pos3[nid][0] for nid in node_ids]
    ny_ = [pos3[nid][1] for nid in node_ids]
    nz_ = [pos3[nid][2] for nid in node_ids]

    node_trace = go.Scatter3d(
        x=nx_, y=ny_, z=nz_,
        mode='markers+text' if n <= 80 else 'markers',
        text=node_labels,
        textposition='top center',
        textfont=dict(size=8, color='white'),
        hovertext=node_hover,
        hoverinfo='text',
        marker=dict(
            size=[s * 0.55 for s in node_sizes],
            color=node_colours,
            line=dict(width=0.5, color='white'),
            opacity=0.88,
        ),
        showlegend=False,
    )

    fig = go.Figure(
        data=[edge_trace, node_trace],
        layout=go.Layout(
            showlegend=False,
            hovermode='closest',
            margin=dict(l=0, r=0, t=0, b=0),
            height=640,
            paper_bgcolor='rgba(0,0,0,0)',
            scene=dict(
                xaxis=dict(showgrid=True, zeroline=True, showticklabels=True,
                           showbackground=True, backgroundcolor='rgb(15,15,25)',
                           gridcolor='rgba(255,255,255,0.08)', title='X',
                           titlefont=dict(color='#aaa'), tickfont=dict(color='#aaa')),
                yaxis=dict(showgrid=True, zeroline=True, showticklabels=True,
                           showbackground=True, backgroundcolor='rgb(15,15,25)',
                           gridcolor='rgba(255,255,255,0.08)', title='Y',
                           titlefont=dict(color='#aaa'), tickfont=dict(color='#aaa')),
                zaxis=dict(showgrid=True, zeroline=True, showticklabels=True,
                           showbackground=True, backgroundcolor='rgb(15,15,25)',
                           gridcolor='rgba(255,255,255,0.08)', title='Z',
                           titlefont=dict(color='#aaa'), tickfont=dict(color='#aaa')),
                bgcolor='rgb(15,15,25)',
                camera=dict(eye=dict(x=1.5, y=1.5, z=0.8)),
            ),
        ),
    )

else:
    # --- 2D edges ---
    edge_traces = []
    for u, v, data in H.edges(data=True):
        x0, y0 = pos2[u]
        x1, y1 = pos2[v]
        opacity = 0.15 + 0.75 * (data['weight'] / w_max)
        edge_traces.append(go.Scatter(
            x=[x0, x1, None], y=[y0, y1, None],
            mode='lines',
            line=dict(width=1.2, color=f'rgba(100,100,120,{opacity:.2f})'),
            hoverinfo='none',
            showlegend=False,
        ))

    # --- 2D nodes ---
    node_x = [pos2[nid][0] for nid in node_ids]
    node_y = [pos2[nid][1] for nid in node_ids]

    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode='markers' if n > 100 else 'markers+text',
        hoverinfo='text',
        hovertext=node_hover,
        text=node_labels,
        textposition='top center',
        textfont=dict(size=9, color='#333'),
        marker=dict(
            size=node_sizes,
            color=node_colours,
            line=dict(width=1, color='white'),
        ),
        showlegend=False,
    )

    fig = go.Figure(
        data=edge_traces + [node_trace],
        layout=go.Layout(
            showlegend=False,
            hovermode='closest',
            margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            height=580,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(248,245,240,1)',
        ),
    )

st.plotly_chart(fig, use_container_width=True)

leg1, leg2, leg3, leg4 = st.columns([1, 1, 1, 3])
leg1.markdown("🔵 Left brain — analytical")
leg2.markdown("🔴 Right brain — creative")
leg3.markdown("🟢 Both / unclassified")
leg4.caption(
    f"{'3D' if mode_3d else '2D'} · "
    f"{H.number_of_nodes()} nodes · {H.number_of_edges()} edges "
    f"(of {g.node_count} / {g.edge_count} total)"
)
