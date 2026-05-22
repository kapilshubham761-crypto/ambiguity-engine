"""
State — live snapshot of the semantic graph.

Sections:
    ① Counters          node count, edge count, avg degree, last cleaned
    ② Ambiguity chart   last 60 scores (variance / cluster / bridge)
    ③ Maintenance       decay/prune controls with dry-run preview
    ④ Top concepts      most-activated nodes table
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json
import streamlit as st
import pandas as pd

from graph import SemanticGraph
from maintenance import run_maintenance, preview_maintenance, DECAY_FACTOR, PRUNE_WEIGHT, ORPHAN_DAYS

st.title("🔮 State")
st.caption("Live snapshot of the semantic graph and recent activity.")


# ======================================================================== #
# Graph load (cached singleton)                                             #
# ======================================================================== #

@st.cache_resource(show_spinner="Loading graph…")
def _load_graph():
    return SemanticGraph()

g = _load_graph()


# ======================================================================== #
# ① Counters                                                                #
# ======================================================================== #

degrees      = [d for _, d in g._g.degree()]
avg_deg      = sum(degrees) / len(degrees) if degrees else 0.0
stamp_path   = os.path.join(ROOT, 'data', 'last_cleaned.txt')
try:
    last_cleaned = open(stamp_path).read().strip()
except Exception:
    last_cleaned = "never"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Nodes",        g.node_count)
c2.metric("Edges",        g.edge_count)
c3.metric("Avg degree",   f"{avg_deg:.2f}")
c4.metric("Last cleaned", last_cleaned)

st.divider()


# ======================================================================== #
# ② Ambiguity score chart                                                   #
# Last 60 inputs — score + all three sub-metrics                           #
# ======================================================================== #

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


# ======================================================================== #
# ③ Maintenance controls                                                    #
# Adjust thresholds → preview affected edges/nodes → apply                 #
# ======================================================================== #

st.subheader("Maintenance")
st.markdown("Adjust thresholds, preview what will be affected, then apply.")

t1, t2, t3 = st.columns(3)
decay_factor = t1.slider(
    "Decay factor (per day)",
    min_value=0.80, max_value=1.00, value=DECAY_FACTOR, step=0.01,
    help="Edge weight multiplied by this value per day since last update. 0.99 = slow fade.",
)
prune_weight = t2.slider(
    "Prune threshold (min edge weight)",
    min_value=0.00, max_value=0.50, value=PRUNE_WEIGHT, step=0.01,
    help="Edges below this weight are deleted after decay.",
)
orphan_days = t3.slider(
    "Orphan cutoff (days)",
    min_value=1, max_value=60, value=ORPHAN_DAYS, step=1,
    help="Nodes with no edges for this many days are removed.",
)

# Dry-run preview
preview     = preview_maintenance(g, decay_factor, prune_weight, orphan_days)
decay_rows  = preview['edges_to_decay']
prune_edges = [r for r in decay_rows if r['will_prune']] + preview['edges_to_prune']
prune_nodes = preview['nodes_to_prune']

with st.expander(
    f"Preview — {len(decay_rows)} edges will decay, "
    f"{len(prune_edges)} edges will be deleted, "
    f"{len(prune_nodes)} nodes will be removed",
    expanded=bool(prune_edges or prune_nodes),
):
    if decay_rows:
        st.markdown("**Edges decaying** (weight before → after)")
        st.dataframe(
            pd.DataFrame(decay_rows)[
                ['concept_a', 'concept_b', 'weight_before', 'weight_after', 'days_old', 'will_prune']
            ],
            use_container_width=True, hide_index=True,
        )
    if prune_nodes:
        st.markdown("**Orphan nodes to be removed**")
        st.dataframe(pd.DataFrame(prune_nodes), use_container_width=True, hide_index=True)
    if not decay_rows and not prune_nodes:
        st.info("Nothing will be changed with current thresholds.")

btn1, btn2 = st.columns(2)
if btn1.button("Apply cleaning now", type="primary", use_container_width=True):
    result = run_maintenance(g, force=True,
                             decay_factor=decay_factor,
                             prune_weight=prune_weight,
                             orphan_days=orphan_days)
    g.save()
    st.cache_resource.clear()
    st.success(
        f"Done — decayed {result['edges_decayed']} edges, "
        f"pruned {result['edges_pruned']} edges, "
        f"removed {result['nodes_pruned']} orphan nodes."
    )
    st.rerun()

if btn2.button("Skip today's auto-clean", use_container_width=True):
    import datetime
    with open(stamp_path, 'w') as f:
        f.write(str(datetime.date.today()))
    st.info("Auto-clean skipped for today.")

st.divider()


# ======================================================================== #
# ④ Most-activated concepts                                                 #
# Top 20 nodes by activation count                                          #
# ======================================================================== #

st.subheader("Most-activated concepts")

if g.node_count > 0:
    nodes = sorted(g.all_nodes(), key=lambda n: n['activation_count'], reverse=True)[:20]
    df_nodes = pd.DataFrame([
        {
            'concept':     n['text'],
            'activations': n['activation_count'],
            'degree':      g._g.degree(n['node_id']),
            'first_seen':  n['first_seen'][:10],
            'last_seen':   n['last_seen'][:10],
        }
        for n in nodes
    ])
    st.dataframe(df_nodes, use_container_width=True, hide_index=True)
else:
    st.info("Graph is empty — run some inputs through the Runner page.")
