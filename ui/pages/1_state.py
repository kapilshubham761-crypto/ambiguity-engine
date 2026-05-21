"""7.2 — State page: counters, ambiguity chart, most-activated concepts."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json
import streamlit as st
import pandas as pd

st.title("🔮 State")
st.caption("Live snapshot of the semantic graph and recent activity.")

# ------------------------------------------------------------------ #
# Load graph                                                           #
# ------------------------------------------------------------------ #
from graph import SemanticGraph

@st.cache_resource(show_spinner="Loading graph…")
def load_graph():
    return SemanticGraph()

g = load_graph()

# ------------------------------------------------------------------ #
# 7.2a — Counters                                                      #
# ------------------------------------------------------------------ #
degrees = [d for _, d in g._g.degree()]
avg_deg = sum(degrees) / len(degrees) if degrees else 0.0

stamp_path = os.path.join(ROOT, 'data', 'last_cleaned.txt')
try:
    last_cleaned = open(stamp_path).read().strip()
except Exception:
    last_cleaned = "never"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Nodes", g.node_count)
c2.metric("Edges", g.edge_count)
c3.metric("Avg degree", f"{avg_deg:.2f}")
c4.metric("Last cleaned", last_cleaned)

st.divider()

# ------------------------------------------------------------------ #
# 7.2b — Ambiguity score chart                                         #
# ------------------------------------------------------------------ #
st.subheader("Recent ambiguity scores")

score_log = os.path.join(ROOT, 'logs', 'ambiguity_scores.jsonl')
if os.path.exists(score_log):
    rows = [json.loads(l) for l in open(score_log, encoding='utf-8') if l.strip()]
    if rows:
        df = pd.DataFrame(rows)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').tail(60)

        st.line_chart(
            df.set_index('timestamp')[['score', 'variance', 'cluster', 'bridge']],
            height=260,
        )

        level_counts = df['level'].value_counts()
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Low",    level_counts.get('low',    0))
        cc2.metric("Medium", level_counts.get('medium', 0))
        cc3.metric("High",   level_counts.get('high',   0))
    else:
        st.info("No scores logged yet — run the Runner page to generate some.")
else:
    st.info("Score log not found. Run the Runner page first.")

st.divider()

# ------------------------------------------------------------------ #
# 7.2c — Most-activated concepts                                       #
# ------------------------------------------------------------------ #
st.subheader("Most-activated concepts")

if g.node_count > 0:
    nodes = sorted(
        g.all_nodes(),
        key=lambda n: n['activation_count'],
        reverse=True,
    )[:20]
    df_nodes = pd.DataFrame([
        {
            'concept':          n['text'],
            'activations':      n['activation_count'],
            'degree':           g._g.degree(n['node_id']),
            'first_seen':       n['first_seen'][:10],
            'last_seen':        n['last_seen'][:10],
        }
        for n in nodes
    ])
    st.dataframe(df_nodes, use_container_width=True, hide_index=True)
else:
    st.info("Graph is empty — run some inputs through the Runner page.")
