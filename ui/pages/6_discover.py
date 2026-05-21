"""6 — Discover: search open libraries, approve/decline, feed to engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st

from graph import SemanticGraph
from extractor import extract
from detector import detect_and_log
from discover import (
    search_sources, fetch_content,
    SOURCE_LABELS, SOURCE_DESCRIPTIONS, SOURCES,
)

st.title("🌐 Discover")
st.caption("Search Wikipedia, arXiv, Gutenberg, Reddit, OpenAlex, and the web. Approve what goes into the engine.")

# ------------------------------------------------------------------ #
# Session state                                                        #
# ------------------------------------------------------------------ #
for key, default in [
    ('queue', []), ('approved', []), ('declined', []), ('fed', []),
]:
    if key not in st.session_state:
        st.session_state[key] = default

@st.cache_resource(show_spinner=False)
def load_graph():
    return SemanticGraph()

g = load_graph()

# ------------------------------------------------------------------ #
# Search controls                                                      #
# ------------------------------------------------------------------ #
with st.expander("🔍 Search settings", expanded=True):
    query = st.text_input("Query", placeholder="e.g.  consciousness and free will  |  protein folding  |  surrealist poetry")

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
    with st.spinner(f'Searching {len(selected_sources)} sources for "{query}"…'):
        new_results = search_sources(query.strip(), selected_sources, max_per_source=max_per)

    existing_urls = {r['url'] for r in st.session_state.queue}
    added = 0
    for r in new_results:
        if r.get('_error'):
            st.warning(f"{r['source']}: {r['snippet']}")
            continue
        if r['url'] and r['url'] not in existing_urls:
            st.session_state.queue.append(r)
            existing_urls.add(r['url'])
            added += 1
    st.toast(f"Added {added} results across {len(selected_sources)} sources.")

# ------------------------------------------------------------------ #
# Stats                                                                #
# ------------------------------------------------------------------ #
s1, s2, s3, s4, s5 = st.columns(5)
s1.metric("Queue",    len(st.session_state.queue))
s2.metric("Approved", len(st.session_state.approved))
s3.metric("Declined", len(st.session_state.declined))
s4.metric("Sentences fed", sum(r.get('sentences_fed', 0) for r in st.session_state.fed))
s5.metric("Graph nodes", g.node_count)

st.divider()

# ------------------------------------------------------------------ #
# Approve / Decline queue                                              #
# ------------------------------------------------------------------ #
SOURCE_COLOURS = {
    'wikipedia': '🔵', 'arxiv': '🟣', 'gutenberg': '🟤',
    'reddit': '🟠', 'openalex': '🟢', 'web': '⚪',
}

if not st.session_state.queue:
    st.info("Queue is empty — run a search above.")
else:
    st.subheader(f"Queue — {len(st.session_state.queue)} pending")
    to_remove = []

    for i, item in enumerate(st.session_state.queue):
        badge = SOURCE_COLOURS.get(item['source'], '⚪')
        label = SOURCE_LABELS.get(item['source'], item['source'])

        with st.container(border=True):
            head_col, badge_col = st.columns([6, 1])
            head_col.markdown(f"**{item['title']}**")
            badge_col.markdown(f"{badge} `{label}`")

            if item['url']:
                st.caption(item['url'])
            if item['snippet']:
                st.write(item['snippet'][:400])

            col_a, col_d, _ = st.columns([2, 2, 4])
            approve = col_a.button("✅ Approve", key=f"a_{i}_{item['url']}", use_container_width=True)
            decline = col_d.button("❌ Decline", key=f"d_{i}_{item['url']}", use_container_width=True)

            if approve:
                if not item.get('url'):
                    st.warning("No URL to fetch.")
                else:
                    with st.spinner(f"Fetching {item['url'][:70]}…"):
                        try:
                            sentences = fetch_content(item)
                        except Exception as e:
                            sentences = []
                            st.error(f"Fetch failed: {e}")

                    if not sentences:
                        st.warning("No sentences extracted — page may be paywalled or empty.")
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
                        to_remove.append(i)
                        st.success(
                            f"Fed **{len(sentences)}** sentences · "
                            f"**{n_concepts}** concept hits · "
                            f"graph now **{g.node_count}** nodes / **{g.edge_count}** edges"
                        )

            if decline:
                st.session_state.declined.append(item)
                to_remove.append(i)

    for i in sorted(set(to_remove), reverse=True):
        st.session_state.queue.pop(i)
    if to_remove:
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
