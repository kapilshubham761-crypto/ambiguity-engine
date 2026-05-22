"""Graph view — 2D / 3D force-directed network with left/right brain filter and split layout."""

# ============================================================ #
#  Imports                                                      #
# ============================================================ #
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from collections import defaultdict

import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import numpy as np

from graph import SemanticGraph


# ============================================================ #
#  Brain-side classifier                                        #
#  Each node text is checked against two keyword sets.         #
#  Results: 'left' | 'right' | 'both' | 'none'                #
# ============================================================ #

_LEFT_KEYWORDS = {
    'logic', 'analysis', 'analytic', 'algorithm', 'data', 'math',
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
    'intuition', 'metaphor', 'story', 'narrative', 'image', 'colour',
    'beauty', 'aesthetic', 'poetry', 'poem', 'rhythm', 'melody', 'harmony',
    'symbol', 'myth', 'legend', 'spiritual', 'consciousness', 'awareness',
    'empathy', 'compassion', 'joy', 'fear', 'love', 'grief', 'wonder',
    'fantasy', 'vision', 'sense', 'perception', 'body', 'gesture', 'dance',
    'play', 'humour', 'irony', 'paradox', 'ambiguity', 'mystery', 'holistic',
    'spontaneous', 'random', 'chaos', 'flow', 'subconscious',
    'identity', 'self', 'soul', 'meaning', 'purpose', 'experience', 'qualia',
}

# Colour per side — used for node fill
_SIDE_COLOUR = {
    'left':  '#4a9eff',   # blue   — analytical
    'right': '#ff6b6b',   # red    — creative
    'both':  '#f5a623',   # orange — bridge
    'none':  '#666677',   # grey   — unclassified
}

# x-axis bands used in split layout
_SPLIT_X_BANDS = {
    'left':  (-1.5, -0.6),
    'both':  (-0.35, 0.35),
    'none':  (-0.35, 0.35),
    'right': (0.6,   1.5),
}


def _brain_side(text: str) -> str:
    words    = set(text.lower().replace('-', ' ').split())
    is_left  = bool(words & _LEFT_KEYWORDS)
    is_right = bool(words & _RIGHT_KEYWORDS)
    if is_left and is_right:
        return 'both'
    if is_left:
        return 'left'
    if is_right:
        return 'right'
    return 'none'


# ============================================================ #
#  Page header                                                  #
# ============================================================ #

st.title("🕸️ Graph")
st.caption("Force-directed view of the semantic graph. Node size = activation count. Edge opacity = weight.")


# ============================================================ #
#  Load graph (cached singleton)                               #
# ============================================================ #

@st.cache_resource(show_spinner="Loading graph…")
def _load_graph():
    return SemanticGraph()

g = _load_graph()

if g.node_count == 0:
    st.info("Graph is empty — run some inputs through the Runner page first.")
    st.stop()


# ============================================================ #
#  Session state — view toggles                                #
# ============================================================ #

if 'graph_3d'    not in st.session_state:
    st.session_state.graph_3d    = False
if 'graph_split' not in st.session_state:
    st.session_state.graph_split = False


# ============================================================ #
#  Controls row                                                 #
# ============================================================ #

ctrl1, ctrl2, ctrl3, ctrl4, ctrl5, ctrl6 = st.columns([2, 2, 2, 1, 1, 1])

_n         = max(g.node_count, 100)
min_weight = ctrl1.slider("Min edge weight", 0.0, 1.0, 0.05, 0.01)
max_nodes  = ctrl2.slider("Max nodes", 10, max(1000, _n), max(10, min(1000, _n)))
brain_mode = ctrl3.radio(
    "Brain filter",
    ["🌐 All", "◀️ Left", "▶️ Right"],
    horizontal=True,
    label_visibility='collapsed',
)

with ctrl4:
    st.write("")
    _3d_label = "⬛ 2D" if st.session_state.graph_3d else "🧊 3D"
    if st.button(_3d_label, use_container_width=True, help="Toggle 2D / 3D view"):
        st.session_state.graph_3d = not st.session_state.graph_3d
        st.rerun()

with ctrl5:
    st.write("")
    _split_label = "🔀 Merge" if st.session_state.graph_split else "🧠 Split"
    if st.button(_split_label, use_container_width=True,
                 help="Separate left / right brain nodes into distinct regions"):
        st.session_state.graph_split = not st.session_state.graph_split
        st.rerun()

with ctrl6:
    st.write("")
    if st.button("↺ Reset", use_container_width=True, help="Return to default 2D view"):
        st.session_state.graph_3d    = False
        st.session_state.graph_split = False
        st.rerun()

# Resolved flags (split is 2D-only)
mode_3d     = st.session_state.graph_3d
graph_split = st.session_state.graph_split and not mode_3d


# ============================================================ #
#  Build subgraph                                               #
#  1. Sort all nodes by activation count (most active first)   #
#  2. Apply brain filter                                        #
#  3. Slice to max_nodes                                        #
#  4. Remove low-weight edges                                   #
# ============================================================ #

_brain_filter = {'🌐 All': 'all', '◀️ Left': 'left', '▶️ Right': 'right'}[brain_mode]

top_nodes = sorted(
    g._g.nodes(data=True),
    key=lambda x: x[1]['activation_count'],
    reverse=True,
)

# left  → keep left + both (bridge nodes connect the hemispheres)
# right → keep right + both
# all   → keep everything
if _brain_filter == 'left':
    top_nodes = [(nid, d) for nid, d in top_nodes
                 if _brain_side(d['text']) in ('left', 'both')]
elif _brain_filter == 'right':
    top_nodes = [(nid, d) for nid, d in top_nodes
                 if _brain_side(d['text']) in ('right', 'both')]

top_nodes = top_nodes[:max_nodes]
sub_ids   = {nid for nid, _ in top_nodes}

H = g._g.subgraph(sub_ids).copy()
H.remove_edges_from(
    [(u, v) for u, v, d in H.edges(data=True) if d['weight'] < min_weight]
)

if H.number_of_nodes() == 0:
    st.warning("No nodes to display with current filters.")
    st.stop()


# ============================================================ #
#  Common node data                                             #
#  (computed once; used by both layout and figure builders)    #
# ============================================================ #

node_ids       = list(H.nodes())
brain_side_map = {nid: _brain_side(H.nodes[nid]['text']) for nid in node_ids}

acts    = [H.nodes[nid]['activation_count'] for nid in node_ids]
act_max = max(acts) if acts else 1

weights = [d['weight'] for _, _, d in H.edges(data=True)]
w_max   = max(weights) if weights else 1.0

node_sizes   = [8 + 18 * (a / act_max) for a in acts]
node_colours = [_SIDE_COLOUR[brain_side_map[nid]] for nid in node_ids]
node_labels  = [H.nodes[nid]['text'] for nid in node_ids]

node_hover = [
    f"<b>{H.nodes[nid]['text']}</b><br>"
    f"side={brain_side_map[nid]}  "
    f"activations={H.nodes[nid]['activation_count']}  degree={H.degree(nid)}<br>"
    f"first={H.nodes[nid]['first_seen'][:10]}  last={H.nodes[nid]['last_seen'][:10]}"
    for nid in node_ids
]

n = H.number_of_nodes()


# ============================================================ #
#  Layout — compute 2D or 3D positions                         #
# ============================================================ #

if mode_3d:
    if n > 300:
        # Large graph: random 3D scatter (spring_layout too slow)
        rng        = np.random.default_rng(42)
        coords     = rng.uniform(-1, 1, (n, 3))
        pos3       = {nid: coords[i] for i, nid in enumerate(node_ids)}
    else:
        pos2 = nx.spring_layout(H, seed=42, k=1.2)
        rng  = np.random.default_rng(42)
        pos3 = {nid: np.append(xy, rng.uniform(-1, 1)) for nid, xy in pos2.items()}
else:
    pos2 = nx.random_layout(H, seed=42) if n > 300 else nx.spring_layout(H, seed=42, k=1.2)


# ============================================================ #
#  Split-brain layout (2D only)                                #
#  Rescale each group's x into its designated band so left     #
#  nodes sit left, right nodes sit right, bridges are centred. #
# ============================================================ #

if graph_split:
    groups = defaultdict(list)
    for nid in node_ids:
        groups[brain_side_map[nid]].append(nid)

    for side, members in groups.items():
        if not members:
            continue
        x_lo, x_hi = _SPLIT_X_BANDS[side]
        xs   = [pos2[nid][0] for nid in members]
        mn   = min(xs)
        span = (max(xs) - mn) or 1.0
        for nid in members:
            norm       = (pos2[nid][0] - mn) / span          # normalise to 0..1
            pos2[nid]  = np.array([x_lo + norm * (x_hi - x_lo), pos2[nid][1]])


# ============================================================ #
#  Build Plotly figure                                          #
# ============================================================ #

if mode_3d:

    # --- edges ---
    edge_x, edge_y, edge_z = [], [], []
    for u, v in H.edges():
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

    # --- nodes ---
    node_trace = go.Scatter3d(
        x=[pos3[nid][0] for nid in node_ids],
        y=[pos3[nid][1] for nid in node_ids],
        z=[pos3[nid][2] for nid in node_ids],
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
                bgcolor='rgb(15,15,25)',
                camera=dict(eye=dict(x=1.5, y=1.5, z=0.8)),
                xaxis=dict(
                    showgrid=True, zeroline=True, showticklabels=True,
                    showbackground=True, backgroundcolor='rgb(15,15,25)',
                    gridcolor='rgba(255,255,255,0.08)',
                    title=dict(text='X', font=dict(color='#aaa')),
                    tickfont=dict(color='#aaa'),
                ),
                yaxis=dict(
                    showgrid=True, zeroline=True, showticklabels=True,
                    showbackground=True, backgroundcolor='rgb(15,15,25)',
                    gridcolor='rgba(255,255,255,0.08)',
                    title=dict(text='Y', font=dict(color='#aaa')),
                    tickfont=dict(color='#aaa'),
                ),
                zaxis=dict(
                    showgrid=True, zeroline=True, showticklabels=True,
                    showbackground=True, backgroundcolor='rgb(15,15,25)',
                    gridcolor='rgba(255,255,255,0.08)',
                    title=dict(text='Z', font=dict(color='#aaa')),
                    tickfont=dict(color='#aaa'),
                ),
            ),
        ),
    )

else:  # 2D

    # --- edges (one trace per edge so opacity encodes weight) ---
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

    # --- nodes ---
    node_trace = go.Scatter(
        x=[pos2[nid][0] for nid in node_ids],
        y=[pos2[nid][1] for nid in node_ids],
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

    # --- split-mode: shaded region bands + header labels ---
    split_shapes = []
    split_annots = []
    if graph_split:
        split_shapes = [
            dict(type='rect', xref='x', yref='paper',
                 x0=-1.55, x1=-0.55, y0=0, y1=1,
                 fillcolor='rgba(74,158,255,0.07)', line_width=0),
            dict(type='rect', xref='x', yref='paper',
                 x0=-0.45, x1=0.45, y0=0, y1=1,
                 fillcolor='rgba(200,200,200,0.06)', line_width=0),
            dict(type='rect', xref='x', yref='paper',
                 x0=0.55, x1=1.55, y0=0, y1=1,
                 fillcolor='rgba(255,107,107,0.07)', line_width=0),
        ]
        split_annots = [
            dict(x=-1.05, y=1.04, xref='x', yref='paper', showarrow=False,
                 text='◀ LEFT BRAIN', font=dict(size=11, color='#4a9eff'), xanchor='center'),
            dict(x=0.0,   y=1.04, xref='x', yref='paper', showarrow=False,
                 text='⬛ BRIDGE', font=dict(size=11, color='#888'), xanchor='center'),
            dict(x=1.05,  y=1.04, xref='x', yref='paper', showarrow=False,
                 text='RIGHT BRAIN ▶', font=dict(size=11, color='#ff6b6b'), xanchor='center'),
        ]

    fig = go.Figure(
        data=edge_traces + [node_trace],
        layout=go.Layout(
            showlegend=False,
            hovermode='closest',
            margin=dict(l=0, r=0, t=30 if graph_split else 0, b=0),
            height=580,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(248,245,240,1)',
            xaxis=dict(
                showgrid=False, zeroline=False, showticklabels=False,
                range=[-1.6, 1.6] if graph_split else None,
            ),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            shapes=split_shapes,
            annotations=split_annots,
        ),
    )


# ============================================================ #
#  Render                                                       #
# ============================================================ #

st.plotly_chart(fig, use_container_width=True)

# Legend + stats row
leg1, leg2, leg3, leg4, leg5 = st.columns([1, 1, 1, 1, 2])
leg1.markdown("🔵 Left — analytical")
leg2.markdown("🔴 Right — creative")
leg3.markdown("🟠 Bridge — both")
leg4.markdown("⚫ General")

_mode_label = '3D' if mode_3d else ('2D split' if graph_split else '2D')
leg5.caption(
    f"{_mode_label} · "
    f"{H.number_of_nodes()} nodes · {H.number_of_edges()} edges "
    f"(of {g.node_count} / {g.edge_count} total)"
)
