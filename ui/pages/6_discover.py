"""6 — Discover: autonomous queue + manual search. Approve/decline to feed engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st

from graph import SemanticGraph
from extractor import extract
from detector import detect_and_log
from discover import fetch_content, SOURCE_LABELS, SOURCE_DESCRIPTIONS, SOURCES
from discover import search_sources
from auto_discover import AutoDiscovery, CURRICULUM

st.title("🌐 Discover")
st.caption("Developmental curriculum — content advances from toddler foundations to graduate frontier.")

# ------------------------------------------------------------------ #
# Singletons                                                           #
# ------------------------------------------------------------------ #

@st.cache_resource(show_spinner=False)
def load_graph():
    return SemanticGraph()

@st.cache_resource(show_spinner=False)
def load_auto() -> AutoDiscovery:
    ad = AutoDiscovery()
    g  = load_graph()
    ad.start(graph=g)
    return ad

g  = load_graph()
ad = load_auto()

# ------------------------------------------------------------------ #
# Curriculum stage selector                                            #
# ------------------------------------------------------------------ #
stage_labels = [s['label'] for s in CURRICULUM]
current      = ad.stage

st.markdown("### Curriculum stage")
sel_col, adv_col, info_col = st.columns([3, 1, 4])

with sel_col:
    chosen = st.selectbox(
        "Stage", options=list(range(len(CURRICULUM))),
        format_func=lambda i: CURRICULUM[i]['label'],
        index=current,
        label_visibility='collapsed',
    )
    if chosen != current:
        with st.spinner("Switching stage — fetching new content…"):
            ad.set_stage(chosen)
        st.rerun()

with adv_col:
    st.write("")
    if st.button("Next stage →", use_container_width=True,
                 disabled=current >= len(CURRICULUM) - 1):
        with st.spinner("Advancing…"):
            ad.advance_stage()
        st.rerun()

with info_col:
    st.info(f"**{CURRICULUM[current]['label']}** — {CURRICULUM[current]['description']}")

# Progress bar
st.progress((current) / (len(CURRICULUM) - 1), text=f"Stage {current + 1} of {len(CURRICULUM)}")

st.divider()

# ------------------------------------------------------------------ #
# Session state                                                        #
# ------------------------------------------------------------------ #
for key, default in [('approved', []), ('declined', []), ('fed', [])]:
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------------------------------------------ #
# Header stats + controls                                              #
# ------------------------------------------------------------------ #
queue = ad.queue

col_q, col_a, col_d, col_fed, col_nodes, col_shuf = st.columns([1,1,1,1,1,2])
col_q.metric("Queue",    len(queue))
col_a.metric("Approved", len(st.session_state.approved))
col_d.metric("Declined", len(st.session_state.declined))
col_fed.metric("Sentences fed", sum(r.get('sentences_fed', 0) for r in st.session_state.fed))
col_nodes.metric("Graph nodes", g.node_count)

with col_shuf:
    st.write("")
    sc1, sc2 = st.columns(2)
    if sc1.button("🔀 Shuffle", use_container_width=True,
                  help="Discard current queue and fetch a fresh batch"):
        with st.spinner("Fetching new batch…"):
            ad.shuffle()
        st.rerun()
    sc2.caption(f"Last refill: {ad.last_refill or '—'}  \nStatus: {ad.status}")

st.divider()

# ------------------------------------------------------------------ #
# Autonomous queue                                                     #
# ------------------------------------------------------------------ #
SOURCE_COLOURS = {
    'wikipedia': '🔵', 'arxiv': '🟣', 'gutenberg': '🟤',
    'reddit': '🟠', 'openalex': '🟢', 'web': '⚪',
}

if not queue:
    st.info("Queue is empty — background worker is searching, or click 🔀 Shuffle to fetch now.")
else:
    to_remove_urls = []
    COLS = 4

    for row_start in range(0, len(queue), COLS):
        row_items = queue[row_start:row_start + COLS]
        cols = st.columns(COLS)

        for col, (i, item) in zip(cols, [(row_start + j, it) for j, it in enumerate(row_items)]):
            badge = SOURCE_COLOURS.get(item['source'], '⚪')
            label = SOURCE_LABELS.get(item['source'], item['source'])
            query = item.get('_query', '')

            with col:
                with st.container(border=True):
                    st.markdown(f"**{item['title'][:60]}{'…' if len(item['title'])>60 else ''}**")
                    st.caption(f"{badge} `{label}`" + (f"  ·  *{query[:30]}…*" if len(query)>30 else f"  ·  *{query}*"))
                    if item.get('snippet'):
                        st.write(item['snippet'][:160] + '…')

                    approve = st.button("✅ Approve", key=f"a_{i}_{item['url']}", use_container_width=True)
                    decline = st.button("❌ Decline", key=f"d_{i}_{item['url']}", use_container_width=True)

                    if approve:
                        if not item.get('url'):
                            st.warning("No URL to fetch.")
                        else:
                            with st.spinner(f"Fetching…"):
                                try:
                                    sentences = fetch_content(item)
                                except Exception as e:
                                    sentences = []
                                    st.error(f"Fetch failed: {e}")

                            if not sentences:
                                st.warning("Nothing extracted.")
                            else:
                                n_concepts = 0
                                for sent in sentences:
                                    concepts = extract(sent)
                                    if concepts:
                                        detect_and_log(sent, concepts, graph=g)
                                        g.update(concepts)
                                        n_concepts += len(concepts)
                                g.save()
                                st.cache_resource.clear()

                                st.session_state.approved.append(item)
                                st.session_state.fed.append({
                                    'title':         item['title'],
                                    'url':           item['url'],
                                    'source':        item['source'],
                                    'sentences_fed': len(sentences),
                                    'concepts':      n_concepts,
                                })
                                to_remove_urls.append(item['url'])
                                st.success(f"Fed {len(sentences)} sentences · {n_concepts} hits")

                    if decline:
                        st.session_state.declined.append(item)
                        to_remove_urls.append(item['url'])

    for url in set(to_remove_urls):
        ad.remove(url)
    if to_remove_urls:
        st.rerun()

# ------------------------------------------------------------------ #
# Manual search (collapsed by default)                                 #
# ------------------------------------------------------------------ #
with st.expander("🔍 Manual search", expanded=False):
    query = st.text_input("Query", placeholder="e.g. protein folding  |  surrealist poetry")

    st.markdown("**Sources**")
    source_cols = st.columns(len(SOURCES))
    selected_sources = []
    defaults = {'wikipedia', 'arxiv', 'gutenberg', 'reddit', 'openalex', 'web'}
    for col, (src_key, label) in zip(source_cols, SOURCE_LABELS.items()):
        if col.checkbox(label, value=(src_key in defaults), key=f'src_{src_key}',
                        help=SOURCE_DESCRIPTIONS[src_key]):
            selected_sources.append(src_key)

    col_n, col_btn = st.columns([2, 1])
    max_per = col_n.slider("Results per source", 3, 10, 5)
    do_search = col_btn.button("Search", type="primary", use_container_width=True,
                               disabled=not query.strip() or not selected_sources)

    if do_search and query.strip() and selected_sources:
        with st.spinner(f'Searching {len(selected_sources)} sources…'):
            new_results = search_sources(query.strip(), selected_sources, max_per_source=max_per)

        existing_urls = {r['url'] for r in ad.queue}
        added = 0
        manual_items = []
        for r in new_results:
            if r.get('_error'):
                st.warning(f"{r['source']}: {r['snippet']}")
                continue
            if r['url'] and r['url'] not in existing_urls:
                r['_query'] = query.strip()
                manual_items.append(r)
                existing_urls.add(r['url'])
                added += 1

        if manual_items:
            import json as _json, threading as _t
            with ad._lock:
                ad._queue = manual_items + ad._queue
                ad._save()
        st.toast(f"Added {added} results to queue.")
        st.rerun()

# ------------------------------------------------------------------ #
# Fed log                                                              #
# ------------------------------------------------------------------ #
if st.session_state.fed:
    st.divider()
    st.subheader("Fed this session")
    for entry in reversed(st.session_state.fed):
        badge = SOURCE_COLOURS.get(entry['source'], '⚪')
        st.markdown(
            f"{badge} **{entry['title']}** &nbsp;·&nbsp; "
            f"{entry['sentences_fed']} sentences &nbsp;·&nbsp; "
            f"{entry['concepts']} concept hits  \n"
            f"<small>{entry['url']}</small>",
            unsafe_allow_html=True,
        )
