"""
Timeline — graph growth chart + daily snapshot inspector.

Sections:
    ① Growth chart    continuous node/edge log (growth_log.jsonl)
    ② Snapshot        inspect any daily JSON snapshot (snapshots/)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json
import streamlit as st
import pandas as pd

st.title("📅 Timeline")
st.caption("Daily graph snapshots — watch the semantic graph grow and shift over time.")

snap_dir   = os.path.join(ROOT, 'snapshots')
snap_files = sorted([
    f for f in os.listdir(snap_dir) if f.endswith('.json')
]) if os.path.isdir(snap_dir) else []


# ======================================================================== #
# ① Growth chart                                                            #
# Reads growth_log.jsonl — appended every teacher cycle on node change.   #
# ======================================================================== #

st.subheader("Graph growth over time")

growth_path = os.path.join(ROOT, 'data', 'growth_log.jsonl')
growth_rows = []
try:
    for line in open(growth_path, encoding='utf-8'):
        line = line.strip()
        if line:
            entry = json.loads(line)
            growth_rows.append({
                'time':  entry['ts'],
                'nodes': entry['nodes'],
                'edges': entry['edges'],
            })
except Exception:
    pass

if growth_rows:
    df = pd.DataFrame(growth_rows).set_index('time')
    st.line_chart(df, height=260)
    last  = growth_rows[-1]
    first = growth_rows[0]
    g1, g2, g3 = st.columns(3)
    g1.metric("Current nodes", f"{last['nodes']:,}")
    g2.metric("Current edges", f"{last['edges']:,}")
    g3.metric("Growth", f"+{last['nodes'] - first['nodes']:,} nodes",
              delta=f"+{last['nodes'] - first['nodes']:,}")
else:
    st.info("No growth data yet — the engine logs a snapshot each cycle. Check back shortly.")

st.divider()


# ======================================================================== #
# ② Snapshot inspector                                                      #
# Browse any dated JSON snapshot from the snapshots/ directory.            #
# ======================================================================== #

st.subheader("Snapshot inspector")

if not snap_files:
    st.info("No snapshots yet. Run the Runner page to generate today's snapshot.")
else:
    if len(snap_files) > 1:
        selected = st.select_slider("Date", options=snap_files, value=snap_files[-1])
    else:
        selected = snap_files[-1]
        st.caption(f"Only one snapshot: **{selected}**")

    snap = json.load(open(os.path.join(snap_dir, selected), encoding='utf-8'))

    s1, s2 = st.columns(2)
    s1.metric("Nodes", snap['node_count'])
    s2.metric("Edges", snap['edge_count'])

    tab_nodes, tab_edges = st.tabs(["Nodes", "Edges"])

    with tab_nodes:
        if snap['nodes']:
            df_n = pd.DataFrame(snap['nodes'])
            cols = [c for c in ['text', 'activation_count', 'first_seen', 'last_seen']
                    if c in df_n.columns]
            st.dataframe(df_n[cols].sort_values('activation_count', ascending=False),
                         use_container_width=True, hide_index=True)
        else:
            st.info("No nodes in this snapshot.")

    with tab_edges:
        if snap['edges']:
            df_e = pd.DataFrame(snap['edges'])
            cols = [c for c in ['source', 'target', 'weight', 'edge_type', 'last_updated']
                    if c in df_e.columns]
            st.dataframe(df_e[cols].sort_values('weight', ascending=False),
                         use_container_width=True, hide_index=True)
        else:
            st.info("No edges in this snapshot.")
