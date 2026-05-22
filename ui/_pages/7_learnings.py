"""
Learnings — browse what the engine has absorbed.

Tabs:
    🧠 Concepts         searchable table of all known concepts
    🔗 Connections      weighted edge list + strongest pairs
    🫧 Clusters         greedy modularity communities (emerging topics)
    ⚡ Ambiguity moments every scored input with variance/cluster/bridge
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json
import streamlit as st
import pandas as pd
import networkx as nx

from graph import SemanticGraph

st.title("📖 Learnings")
st.caption("Everything the engine has absorbed — concepts, connections, clusters, and ambiguity moments.")

@st.cache_resource(show_spinner="Loading graph…")
def load_graph():
    return SemanticGraph()

g = load_graph()

if g.node_count == 0:
    st.info("Nothing learned yet — feed some content through the Discover or Runner page.")
    st.stop()

tab_concepts, tab_connections, tab_clusters, tab_moments = st.tabs([
    "🧠 Concepts", "🔗 Connections", "🫧 Clusters", "⚡ Ambiguity moments"
])

# ------------------------------------------------------------------ #
# Tab 1 — Concepts                                                     #
# ------------------------------------------------------------------ #
with tab_concepts:
    st.subheader(f"{g.node_count} concepts learned")

    nodes = sorted(g.all_nodes(), key=lambda n: n['activation_count'], reverse=True)

    search = st.text_input("Search concepts", placeholder="e.g. memory, gravity, emotion…")
    if search.strip():
        nodes = [n for n in nodes if search.lower() in n['text'].lower()]
        st.caption(f"{len(nodes)} matching '{search}'")

    col_sort, col_n = st.columns([2, 1])
    sort_by = col_sort.selectbox("Sort by", ["activations", "degree", "first seen", "last seen"],
                                  label_visibility='collapsed')
    top_n   = col_n.number_input("Show", min_value=10, max_value=g.node_count, value=min(50, g.node_count), step=10)

    if sort_by == "degree":
        nodes = sorted(nodes, key=lambda n: g._g.degree(n['node_id']), reverse=True)
    elif sort_by == "first seen":
        nodes = sorted(nodes, key=lambda n: n['first_seen'])
    elif sort_by == "last seen":
        nodes = sorted(nodes, key=lambda n: n['last_seen'], reverse=True)

    nodes = nodes[:int(top_n)]

    rows = []
    for n in nodes:
        deg = g._g.degree(n['node_id'])
        neighbours = [g._g.nodes[nb]['text']
                      for nb in g._g.neighbors(n['node_id'])]
        rows.append({
            'concept':      n['text'],
            'activations':  n['activation_count'],
            'connections':  deg,
            'first seen':   n['first_seen'][:10],
            'last seen':    n['last_seen'][:10],
            'neighbours':   ', '.join(sorted(neighbours)[:6]) + ('…' if len(neighbours) > 6 else ''),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={
                     'activations': st.column_config.ProgressColumn('activations', max_value=max(r['activations'] for r in rows) or 1),
                     'connections': st.column_config.NumberColumn('connections'),
                 })

# ------------------------------------------------------------------ #
# Tab 2 — Connections                                                  #
# ------------------------------------------------------------------ #
with tab_connections:
    st.subheader(f"{g.edge_count} connections")

    edges = sorted(g.all_edges(), key=lambda e: e['weight'], reverse=True)

    min_w = st.slider("Minimum weight", 0.0, 1.0, 0.05, 0.01)
    edges = [e for e in edges if e['weight'] >= min_w]
    st.caption(f"{len(edges)} connections above weight {min_w:.2f}")

    rows = []
    for e in edges[:200]:
        a = g._g.nodes[e['source']].get('text', e['source'])
        b = g._g.nodes[e['target']].get('text', e['target'])
        rows.append({
            'concept A':    a,
            'concept B':    b,
            'weight':       round(e['weight'], 4),
            'last updated': e.get('last_updated', '')[:10],
        })

    if rows:
        df_e = pd.DataFrame(rows)
        st.dataframe(df_e, use_container_width=True, hide_index=True,
                     column_config={
                         'weight': st.column_config.ProgressColumn('weight', max_value=1.0),
                     })

        st.divider()
        st.markdown("**Strongest pairs**")
        for r in rows[:10]:
            st.markdown(
                f"**{r['concept A']}** ↔ **{r['concept B']}** &nbsp; "
                f"`{r['weight']:.3f}`"
            )
    else:
        st.info("No connections above that weight threshold.")

# ------------------------------------------------------------------ #
# Tab 3 — Clusters                                                     #
# ------------------------------------------------------------------ #
with tab_clusters:
    st.subheader("Concept clusters")
    st.caption("Groups of concepts that are tightly interconnected — the engine's emerging 'topics'.")

    try:
        communities = list(nx.community.greedy_modularity_communities(g._g))
        communities = sorted(communities, key=len, reverse=True)

        st.metric("Clusters found", len(communities))

        for idx, community in enumerate(communities[:20]):
            members = sorted(
                community,
                key=lambda nid: g._g.nodes[nid].get('activation_count', 0),
                reverse=True,
            )
            labels = [g._g.nodes[nid]['text'] for nid in members]
            top_label = labels[0] if labels else f"Cluster {idx+1}"

            with st.expander(f"**{top_label}** cluster — {len(members)} concepts"):
                cols = st.columns(3)
                for i, label in enumerate(labels):
                    act = g._g.nodes[members[i]].get('activation_count', 0)
                    deg = g._g.degree(members[i])
                    cols[i % 3].markdown(f"`{label}` &nbsp; ×{act} · {deg}↔")
    except Exception as e:
        st.warning(f"Could not compute clusters: {e}")

# ------------------------------------------------------------------ #
# Tab 4 — Ambiguity moments                                            #
# ------------------------------------------------------------------ #
with tab_moments:
    st.subheader("Ambiguity moments")
    st.caption("Every input the engine has processed, with its ambiguity score and level.")

    score_log = os.path.join(ROOT, 'logs', 'ambiguity_scores.jsonl')
    if not os.path.exists(score_log):
        st.info("No ambiguity log yet — run something through the Runner page.")
        st.stop()

    entries = [json.loads(l) for l in open(score_log, encoding='utf-8') if l.strip()]
    entries.reverse()

    LEVEL_COLOUR = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}
    level_filter = st.multiselect("Filter by level", ['low', 'medium', 'high'],
                                   default=['low', 'medium', 'high'])
    entries = [e for e in entries if e.get('level') in level_filter]
    st.caption(f"{len(entries)} entries")

    for e in entries[:100]:
        lvl    = e.get('level', '?')
        score  = e.get('score', 0)
        text   = e.get('input_text', '')
        ts     = e.get('timestamp', '')[:16].replace('T', ' ')
        bridge = e.get('bridge', 0)
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{LEVEL_COLOUR.get(lvl, '⚪')} **{text[:120]}{'…' if len(text)>120 else ''}**")
            c2.markdown(f"`{score:.2f}` `{lvl}`")
            st.caption(
                f"bridge {bridge:.2f} · "
                f"variance {e.get('variance',0):.2f} · "
                f"cluster {e.get('cluster',0):.2f} · "
                f"{ts}"
            )
