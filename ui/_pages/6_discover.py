"""6 — Learn: unified lesson intake. Curriculum stage + pre-fetched queue."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import uuid
import streamlit as st
from datetime import datetime, timezone

from graph import SemanticGraph
from teacher import Teacher, REGIONS, _GRID, load_prefs, save_prefs
from auto_discover import CURRICULUM
from discover import search_sources, fetch_content, SOURCE_LABELS, SOURCE_DESCRIPTIONS, SOURCES

st.title("📚 Learn")
st.caption("Pre-fetched lessons matched to the curriculum stage. Accept to teach, Reject to skip.")

# ------------------------------------------------------------------ #
# Singletons                                                           #
# ------------------------------------------------------------------ #

@st.cache_resource(show_spinner=False)
def load_graph():
    return SemanticGraph()

@st.cache_resource(show_spinner=False)
def load_teacher() -> Teacher:
    t = Teacher()
    t.start(graph=load_graph())
    return t

g = load_graph()
t = load_teacher()

# ------------------------------------------------------------------ #
# Curriculum stage selector                                            #
# ------------------------------------------------------------------ #

stage = t._current_stage()

st.markdown("### Curriculum stage")
sel_col, adv_col, info_col = st.columns([3, 1, 4])

with sel_col:
    chosen = st.selectbox(
        "Stage", options=list(range(len(CURRICULUM))),
        format_func=lambda i: CURRICULUM[i]['label'],
        index=stage,
        label_visibility='collapsed',
    )
    if chosen != stage:
        with st.spinner("Switching stage — clearing queue and fetching new lessons…"):
            t.set_stage(chosen)
        st.rerun()

with adv_col:
    st.write("")
    if st.button("Next stage →", use_container_width=True,
                 disabled=stage >= len(CURRICULUM) - 1):
        with st.spinner("Advancing…"):
            t.advance_stage()
        st.rerun()

with info_col:
    st.info(f"**{CURRICULUM[stage]['label']}** — {CURRICULUM[stage]['description']}")

st.progress(stage / (len(CURRICULUM) - 1), text=f"Stage {stage + 1} of {len(CURRICULUM)}")

# ------------------------------------------------------------------ #
# Location + Time filters                                              #
# ------------------------------------------------------------------ #

prefs = load_prefs()

with st.expander("🌐 Location & Time filter", expanded=True):
    st.caption("Select a world region and/or year range — lessons will be biased toward that context.")

    # -- Globe grid --
    st.markdown("**Region**")

    # CSS to style selected cell
    st.markdown("""
    <style>
    div[data-testid="stHorizontalBlock"] button[kind="secondary"] {
        opacity: 0.55;
    }
    </style>""", unsafe_allow_html=True)

    # Global button
    is_global = prefs.get('region', 'global') == 'global'
    if st.button(
        f"{'✓ ' if is_global else ''}🌐 Global (no filter)",
        use_container_width=True,
        type="primary" if is_global else "secondary",
        key="reg_global",
    ):
        prefs['region'] = 'global'
        save_prefs(prefs)
        with st.spinner("Clearing queue and refetching with new region…"):
            t.set_stage(t._current_stage())
        st.rerun()

    # 3×4 grid rows
    for row_keys in _GRID:
        cols = st.columns(4)
        for col, key in zip(cols, row_keys):
            if key is None:
                continue
            info    = REGIONS[key]
            active  = prefs.get('region') == key
            label   = f"{'✓ ' if active else ''}{info['label']}"
            if col.button(label, key=f"reg_{key}", use_container_width=True,
                          type="primary" if active else "secondary"):
                prefs['region'] = key
                save_prefs(prefs)
                with st.spinner("Clearing queue and refetching with new region…"):
                    t.set_stage(t._current_stage())
                st.rerun()

    st.divider()

    # -- Year range --
    st.markdown("**Publication year range**")
    use_years = st.checkbox(
        "Filter by year",
        value=prefs.get('year_from') is not None,
        key="use_years",
    )

    if use_years:
        y_col1, y_col2 = st.columns(2)
        y_from = y_col1.number_input(
            "From year", min_value=1800, max_value=2025,
            value=int(prefs.get('year_from') or 2000),
            step=1, key="yr_from",
        )
        y_to = y_col2.number_input(
            "To year", min_value=1800, max_value=2025,
            value=int(prefs.get('year_to') or 2025),
            step=1, key="yr_to",
        )
        y_from, y_to = min(y_from, y_to), max(y_from, y_to)

        # Quick decade buttons
        st.caption("Quick select:")
        decade_cols = st.columns(8)
        decades = [1920, 1940, 1960, 1970, 1980, 1990, 2000, 2010]
        for dc, decade in zip(decade_cols, decades):
            if dc.button(str(decade), key=f"dec_{decade}", use_container_width=True):
                prefs['year_from'] = decade
                prefs['year_to']   = decade + 9
                save_prefs(prefs)
                with st.spinner("Refetching…"):
                    t.set_stage(t._current_stage())
                st.rerun()

        if (y_from != prefs.get('year_from') or y_to != prefs.get('year_to')):
            if st.button("Apply year filter", type="primary"):
                prefs['year_from'] = y_from
                prefs['year_to']   = y_to
                save_prefs(prefs)
                with st.spinner("Refetching with year filter…"):
                    t.set_stage(t._current_stage())
                st.rerun()

        current_range = f"{prefs.get('year_from')}–{prefs.get('year_to')}"
        st.info(f"📅 Fetching content from **{current_range}**")

    else:
        if prefs.get('year_from') is not None:
            prefs['year_from'] = None
            prefs['year_to']   = None
            save_prefs(prefs)

st.divider()

# ------------------------------------------------------------------ #
# Header stats                                                         #
# ------------------------------------------------------------------ #

stats  = t.stats
queue  = t.queue

today_str      = datetime.now(tz=timezone.utc).date().isoformat()
today_sessions = [s for s in stats.get('sessions', []) if s['ts'][:10] == today_str]
today_accepted  = sum(1 for s in today_sessions if s['action'] == 'accept')
today_sentences = sum(s['sentences'] for s in today_sessions if s['action'] == 'accept')

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Ready",           len(queue))
c2.metric("Accepted today",  today_accepted)
c3.metric("Sentences today", today_sentences)
c4.metric("Total accepted",  stats.get('total_accepted', 0))
c5.metric("Total sentences", stats.get('total_sentences', 0))
c6.metric("Graph nodes",     g.node_count)

with c7:
    st.write("")
    if st.button("🔀 Refill now", use_container_width=True,
                 help="Fetch a fresh batch of lessons immediately"):
        with st.spinner("Fetching lessons…"):
            t._refill()
        st.rerun()
    st.caption(f"Status: **{t.status}**")

st.divider()

# ------------------------------------------------------------------ #
# Lesson queue                                                         #
# ------------------------------------------------------------------ #

SOURCE_COLOURS = {
    'wikipedia': '🔵', 'simple_wiki': '🟦', 'arxiv': '🟣', 'gutenberg': '🟤',
    'reddit': '🟠', 'openalex': '🟢', 'web': '⚪',
}

if not queue:
    st.info("No lessons ready yet — background teacher is fetching content. Check back in a moment.")
    if st.button("Fetch now", type="primary"):
        with st.spinner("Pre-fetching lessons…"):
            t._refill()
        st.rerun()
else:
    COLS = 2
    for row_start in range(0, len(queue), COLS):
        row_items = queue[row_start:row_start + COLS]
        cols = st.columns(COLS)

        for col, lesson in zip(cols, row_items):
            badge     = SOURCE_COLOURS.get(lesson['source'], '⚪')
            stage_lbl = lesson.get('stage_label', '')
            hw_tag    = " 📝" if lesson.get('is_homework') else ""

            with col:
                with st.container(border=True):
                    h1, h2 = st.columns([5, 1])
                    h1.markdown(
                        f"**{lesson['title'][:80]}{'…' if len(lesson['title'])>80 else ''}**"
                        f"{hw_tag}"
                    )
                    h2.markdown(f"{badge}")

                    st.caption(
                        f"📚 `{stage_lbl}` &nbsp;·&nbsp; "
                        f"*{lesson['topic']}* &nbsp;·&nbsp; "
                        f"{lesson['sentence_count']} sentences"
                    )

                    with st.expander("Preview", expanded=True):
                        for sent in lesson['sentences'][:5]:
                            st.markdown(f"› {sent}")
                        if lesson['sentence_count'] > 5:
                            st.caption(f"… and {lesson['sentence_count'] - 5} more")

                    if lesson.get('url'):
                        st.caption(lesson['url'])

                    a_col, r_col = st.columns(2)
                    accept = a_col.button(
                        f"✅ Accept ({lesson['sentence_count']})",
                        key=f"acc_{lesson['id']}",
                        use_container_width=True,
                        type="primary",
                    )
                    reject = r_col.button(
                        "❌ Reject",
                        key=f"rej_{lesson['id']}",
                        use_container_width=True,
                    )

                    if accept:
                        with st.spinner("Feeding into graph…"):
                            n_sent = t.accept(lesson['id'], g)
                        st.cache_resource.clear()
                        st.success(f"Fed {n_sent} sentences → graph now {g.node_count} nodes")
                        st.rerun()

                    if reject:
                        t.reject(lesson['id'])
                        st.rerun()

# ------------------------------------------------------------------ #
# Manual search → pre-fetch into queue                                 #
# ------------------------------------------------------------------ #

with st.expander("🔍 Manual search", expanded=False):
    query = st.text_input("Query", placeholder="e.g. shapes and colours  |  simple animals")

    st.markdown("**Sources**")
    source_cols = st.columns(len(SOURCES))
    selected_sources = []
    stage_defaults = set(
        __import__('auto_discover').STAGE_CONFIG[
            min(stage, len(__import__('auto_discover').STAGE_CONFIG) - 1)
        ]['sources']
    )
    for col, (src_key, label) in zip(source_cols, SOURCE_LABELS.items()):
        if col.checkbox(label, value=(src_key in stage_defaults), key=f'src_{src_key}'):
            selected_sources.append(src_key)

    col_n, col_btn = st.columns([2, 1])
    max_per = col_n.slider("Results per source", 2, 8, 3)
    do_search = col_btn.button("Search & pre-fetch", type="primary",
                               use_container_width=True,
                               disabled=not query.strip() or not selected_sources)

    if do_search and query.strip() and selected_sources:
        with st.spinner(f"Searching and pre-fetching content…"):
            raw = search_sources(query.strip(), selected_sources, max_per_source=max_per)
            added = 0
            existing_urls = {l['url'] for l in t.queue if l.get('url')}
            for item in raw:
                if item.get('_error') or not item.get('url'):
                    continue
                if item['url'] in existing_urls:
                    continue
                try:
                    sentences = fetch_content(item)
                except Exception:
                    continue
                if len(sentences) < 3:
                    continue
                lesson = {
                    'id':             str(uuid.uuid4()),
                    'title':          item.get('title', query),
                    'url':            item['url'],
                    'source':         item.get('source', 'web'),
                    'topic':          query.strip(),
                    'stage':          stage,
                    'stage_label':    CURRICULUM[stage]['label'],
                    'sentences':      sentences,
                    'preview':        ' '.join(sentences[:3]),
                    'sentence_count': len(sentences),
                    'fetched_at':     datetime.now(tz=timezone.utc).isoformat(),
                    'is_homework':    False,
                }
                with t._lock:
                    t._queue.append(lesson)
                    t._save_queue()
                existing_urls.add(item['url'])
                added += 1

        st.toast(f"Added {added} pre-fetched lessons to queue.")
        st.rerun()

# ------------------------------------------------------------------ #
# Teaching log                                                         #
# ------------------------------------------------------------------ #

sessions = stats.get('sessions', [])
if sessions:
    st.divider()
    with st.expander("📋 Teaching log", expanded=False):
        log_filter = st.radio("Show", ["Today", "All"], horizontal=True)
        if log_filter == "Today":
            sessions = [s for s in sessions if s['ts'][:10] == today_str]

        for s in reversed(sessions[-50:]):
            action = s['action']
            icon   = "✅" if action == "accept" else "❌"
            ts     = s['ts'][11:16] + " UTC"
            badge  = SOURCE_COLOURS.get(s.get('source', ''), '⚪')
            st.markdown(
                f"{icon} {badge} **{s['title'][:70]}** "
                f"&nbsp;·&nbsp; *{s['topic']}* "
                + (f"&nbsp;·&nbsp; {s['sentences']} sentences · {s['concepts']} concepts"
                   if action == 'accept' else "")
                + f"  \n<small>{ts} &nbsp;·&nbsp; {s.get('stage_label', '')}</small>",
                unsafe_allow_html=True,
            )
