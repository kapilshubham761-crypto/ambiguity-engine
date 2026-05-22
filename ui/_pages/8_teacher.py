"""8 — Teacher: 24-hour autonomous teaching pipeline."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
from datetime import datetime, timezone

from graph import SemanticGraph
from teacher import Teacher
from auto_discover import CURRICULUM

st.title("🎓 Teacher")
st.caption("Pre-fetched lessons, ready to feed instantly. Accept to teach, Reject to skip.")

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
# Header stats                                                         #
# ------------------------------------------------------------------ #
stats  = t.stats
queue  = t.queue
stage  = queue[0]['stage'] if queue else 0
try:
    stage = __import__('json').load(
        open(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'discovery_stage.json'))
    ).get('stage', 0)
except Exception:
    pass

today_sessions = [s for s in stats.get('sessions', [])
                  if s['ts'][:10] == datetime.now(tz=timezone.utc).date().isoformat()]
today_accepted  = sum(1 for s in today_sessions if s['action'] == 'accept')
today_sentences = sum(s['sentences'] for s in today_sessions if s['action'] == 'accept')

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Ready",           len(queue))
c2.metric("Accepted today",  today_accepted)
c3.metric("Sentences today", today_sentences)
c4.metric("Total accepted",  stats.get('total_accepted', 0))
c5.metric("Total sentences", stats.get('total_sentences', 0))
c6.metric("Graph nodes",     g.node_count)

# Status pill
status_col, stage_col = st.columns([1, 3])
status_col.caption(f"🔄 Status: **{t.status}**")
stage_col.info(f"📚 Current stage: **{CURRICULUM[stage]['label']}** — {CURRICULUM[stage]['description']}")

st.progress(stage / (len(CURRICULUM) - 1), text=f"Stage {stage + 1} of {len(CURRICULUM)}")

st.divider()

# ------------------------------------------------------------------ #
# Lesson queue                                                         #
# ------------------------------------------------------------------ #
SOURCE_COLOURS = {
    'wikipedia': '🔵', 'simple_wiki': '🟦', 'arxiv': '🟣', 'gutenberg': '🟤',
    'reddit': '🟠', 'openalex': '🟢', 'web': '⚪',
}

if not queue:
    st.info("No lessons ready yet — background teacher is fetching content. Check back in a moment, or the queue refills automatically every 2 minutes.")
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
            badge  = SOURCE_COLOURS.get(lesson['source'], '⚪')
            stage_lbl = lesson.get('stage_label', '')

            with col:
                with st.container(border=True):
                    # Header
                    h1, h2 = st.columns([5, 1])
                    h1.markdown(f"**{lesson['title'][:80]}{'…' if len(lesson['title'])>80 else ''}**")
                    h2.markdown(f"{badge}")

                    st.caption(
                        f"📚 `{stage_lbl}` &nbsp;·&nbsp; "
                        f"*{lesson['topic']}* &nbsp;·&nbsp; "
                        f"{lesson['sentence_count']} sentences"
                    )

                    # Preview — show first few sentences
                    with st.expander("Preview sentences", expanded=True):
                        for sent in lesson['sentences'][:5]:
                            st.markdown(f"› {sent}")
                        if lesson['sentence_count'] > 5:
                            st.caption(f"… and {lesson['sentence_count'] - 5} more")

                    if lesson.get('url'):
                        st.caption(lesson['url'])

                    # Accept / Reject
                    a_col, r_col = st.columns(2)
                    accept = a_col.button(
                        f"✅ Accept  ({lesson['sentence_count']} sentences)",
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
# Teaching log                                                         #
# ------------------------------------------------------------------ #
sessions = stats.get('sessions', [])
if sessions:
    st.divider()
    st.subheader("Teaching log")

    log_filter = st.radio("Show", ["Today", "All"], horizontal=True)
    today_str  = datetime.now(tz=timezone.utc).date().isoformat()

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
            + (f"&nbsp;·&nbsp; {s['sentences']} sentences · {s['concepts']} concepts" if action == 'accept' else "")
            + f"  \n<small>{ts} &nbsp;·&nbsp; {s.get('stage_label', '')}</small>",
            unsafe_allow_html=True,
        )
