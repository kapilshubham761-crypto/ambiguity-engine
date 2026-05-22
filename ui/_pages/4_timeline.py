"""7.5 — Timeline: flip through daily graph snapshots."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json
import streamlit as st
import pandas as pd

st.title("📅 Timeline")
st.caption("Daily graph snapshots — watch the semantic graph grow and shift over time.")

snap_dir = os.path.join(ROOT, 'snapshots')
snap_files = sorted([
    f for f in os.listdir(snap_dir) if f.endswith('.json')
]) if os.path.isdir(snap_dir) else []

if not snap_files:
    st.info("No snapshots yet. Run the Runner page to generate today's snapshot.")
    st.stop()

# ------------------------------------------------------------------ #
# Growth chart across all snapshots                                    #
# ------------------------------------------------------------------ #
st.subheader("Graph growth over time")

rows = []
for fname in snap_files:
    try:
        snap = json.load(open(os.path.join(snap_dir, fname), encoding='utf-8'))
        rows.append({
            'date':       snap['date'],
            'nodes':      snap['node_count'],
            'edges':      snap['edge_count'],
        })
    except Exception:
        pass

if rows:
    df = pd.DataFrame(rows).set_index('date')
    st.line_chart(df, height=220)

st.divider()

# ------------------------------------------------------------------ #
# Single-snapshot inspector                                            #
# ------------------------------------------------------------------ #
st.subheader("Snapshot inspector")
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
        cols = [c for c in ['text','activation_count','first_seen','last_seen'] if c in df_n.columns]
        st.dataframe(df_n[cols].sort_values('activation_count', ascending=False),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No nodes in this snapshot.")

with tab_edges:
    if snap['edges']:
        df_e = pd.DataFrame(snap['edges'])
        cols = [c for c in ['source','target','weight','edge_type','last_updated'] if c in df_e.columns]
        st.dataframe(df_e[cols].sort_values('weight', ascending=False),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No edges in this snapshot.")
