"""7.3 — Graph view: plotly force-directed network."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import numpy as np

st.title("🕸️ Graph")
st.caption("Force-directed view of the semantic graph. Node size = activation count. Edge opacity = weight.")

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
col1, col2 = st.columns(2)
min_weight  = col1.slider("Min edge weight to show", 0.0, 1.0, 0.05, 0.01)
max_nodes   = col2.slider("Max nodes to show", 10, min(200, g.node_count), min(60, g.node_count))

# Subgraph: keep top-N most-activated nodes
top_nodes = sorted(
    g._g.nodes(data=True),
    key=lambda x: x[1]['activation_count'],
    reverse=True,
)[:max_nodes]
sub_ids = {nid for nid, _ in top_nodes}

H = g._g.subgraph(sub_ids).copy()
H.remove_edges_from([(u, v) for u, v, d in H.edges(data=True) if d['weight'] < min_weight])

if H.number_of_nodes() == 0:
    st.warning("No nodes to display with current filters.")
    st.stop()

# ------------------------------------------------------------------ #
# Layout                                                               #
# ------------------------------------------------------------------ #
pos = nx.spring_layout(H, seed=42, k=1.2)

# ------------------------------------------------------------------ #
# Edges                                                                #
# ------------------------------------------------------------------ #
edge_traces = []
weights = [d['weight'] for _, _, d in H.edges(data=True)]
w_max   = max(weights) if weights else 1.0

for u, v, data in H.edges(data=True):
    x0, y0 = pos[u]
    x1, y1 = pos[v]
    opacity = 0.15 + 0.75 * (data['weight'] / w_max)
    edge_traces.append(go.Scatter(
        x=[x0, x1, None], y=[y0, y1, None],
        mode='lines',
        line=dict(width=1.2, color=f'rgba(100,100,120,{opacity:.2f})'),
        hoverinfo='none',
        showlegend=False,
    ))

# ------------------------------------------------------------------ #
# Nodes                                                                #
# ------------------------------------------------------------------ #
node_x, node_y, node_text, node_size, node_color = [], [], [], [], []
acts = [data['activation_count'] for _, data in H.nodes(data=True)]
act_max = max(acts) if acts else 1

for nid, data in H.nodes(data=True):
    x, y = pos[nid]
    node_x.append(x)
    node_y.append(y)
    deg   = H.degree(nid)
    act   = data['activation_count']
    node_text.append(
        f"<b>{data['text']}</b><br>activations={act}  degree={deg}<br>"
        f"first={data['first_seen'][:10]}  last={data['last_seen'][:10]}"
    )
    node_size.append(8 + 18 * (act / act_max))
    node_color.append(act)

node_trace = go.Scatter(
    x=node_x, y=node_y,
    mode='markers+text',
    hoverinfo='text',
    hovertext=node_text,
    text=[H.nodes[nid]['text'] for nid in H.nodes()],
    textposition='top center',
    textfont=dict(size=9, color='#333'),
    marker=dict(
        size=node_size,
        color=node_color,
        colorscale='Teal',
        showscale=True,
        colorbar=dict(title='Activations', thickness=12),
        line=dict(width=1, color='white'),
    ),
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
st.caption(f"Showing {H.number_of_nodes()} nodes, {H.number_of_edges()} edges "
           f"(filtered from {g.node_count} / {g.edge_count})")
