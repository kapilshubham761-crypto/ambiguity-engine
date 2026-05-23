"""Live Feed — real-time view of what the engine is learning right now."""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
from datetime import datetime, timezone
from pathlib import Path
from graph import SemanticGraph
from learner import AutoLearner, REGIONS, load_prefs, save_prefs, CURRICULUM

st.title("⚡ Live Feed")
st.caption("Real-time learning stream — no queue, no manual review.")

_ROOT = Path(__file__).parent.parent.parent
_DATA = _ROOT / 'data'

# ── Singletons ────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def load_graph():
    return SemanticGraph()

@st.cache_resource(show_spinner=False)
def load_learner() -> AutoLearner:
    l = AutoLearner.get()
    l.start(graph=load_graph())
    return l

g = load_graph()
l = load_learner()

# ── Stats bar ─────────────────────────────────────────────────────────────────

stats = l.stats
stage = l.current_stage()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Stage",      CURRICULUM[stage]['label'][:18])
c2.metric("Accepted",   f"{stats.get('total_accepted', 0):,}")
c3.metric("Sentences",  f"{stats.get('total_sentences', 0):,}")
c4.metric("Concepts",   f"{stats.get('total_concepts', 0):,}")
try:
    import sqlite3
    with sqlite3.connect(_DATA / 'graph.db') as con:
        nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    c5.metric("Graph nodes", f"{nodes:,}")
except Exception:
    c5.metric("Graph nodes", "—")

st.divider()

# ── Controls ──────────────────────────────────────────────────────────────────

col_ctrl, col_stage, col_region = st.columns([2, 3, 3])

with col_ctrl:
    paused = l.is_paused
    if paused:
        if st.button("▶️ Resume", type="primary", use_container_width=True):
            l.resume(); st.rerun()
    else:
        if st.button("⏹ Pause", use_container_width=True):
            l.pause(); st.rerun()

with col_stage:
    from curriculum import CURRICULUM
    chosen_stage = st.selectbox(
        "Curriculum stage",
        options=list(range(len(CURRICULUM))),
        format_func=lambda i: CURRICULUM[i]['label'],
        index=stage,
        label_visibility='collapsed',
    )
    if chosen_stage != stage:
        l.set_stage(chosen_stage); st.rerun()

with col_region:
    prefs = load_prefs()
    region_key = prefs.get('region', 'global')
    region_options = list(REGIONS.keys())
    chosen_region = st.selectbox(
        "Region",
        options=region_options,
        format_func=lambda k: REGIONS[k]['label'],
        index=region_options.index(region_key) if region_key in region_options else 0,
        label_visibility='collapsed',
    )
    if chosen_region != region_key:
        prefs['region'] = chosen_region
        save_prefs(prefs); st.rerun()

st.divider()

# ── Live feed ─────────────────────────────────────────────────────────────────

feed_path = _DATA / 'live_feed.jsonl'
entries = []
if feed_path.exists():
    try:
        lines = feed_path.read_text(encoding='utf-8').splitlines()
        for line in reversed(lines[-60:]):
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass

if not entries:
    if paused:
        st.info("Engine is paused. Press Resume to start learning.")
    else:
        st.info("Engine is starting — first results appear within ~15 seconds.")
else:
    st.markdown(f"**{len(entries)} recent entries** — newest first")

    for e in entries:
        ts_raw = e.get('ts', '')
        try:
            dt  = datetime.fromisoformat(ts_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_s = int((datetime.now(tz=timezone.utc) - dt).total_seconds())
            age   = f"{age_s}s ago" if age_s < 60 else f"{age_s//60}m ago"
        except Exception:
            age = ts_raw

        n_concepts = e.get('concepts', 0)
        source     = e.get('source', '?')
        topic      = e.get('topic', '')
        title      = e.get('title', topic)

        dot_col = '#44ff88' if n_concepts > 10 else '#4a9eff' if n_concepts > 0 else '#555'
        dot     = f'<span style="color:{dot_col};font-size:0.85em">●</span>'

        st.markdown(
            f"""
            <div style="border:1px solid #1e1e2e;border-radius:8px;padding:8px 14px;
                        margin-bottom:6px;background:#0d0d1a">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                {dot}
                <span style="font-weight:600;color:#ddd;font-size:0.9em">{title}</span>
                <span style="color:#555;font-size:0.78em;margin-left:auto">{age}</span>
              </div>
              <div style="display:flex;gap:12px;margin-top:4px;font-size:0.78em;color:#666">
                <span>source: <span style="color:#4a9eff">{source}</span></span>
                <span>topic: <span style="color:#a78bfa">{topic}</span></span>
                <span>+<span style="color:#44ff88;font-weight:600">{n_concepts}</span> concepts</span>
                <span>{e.get('sentences',0)} sentences</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ── Auto-refresh ──────────────────────────────────────────────────────────────

st.divider()
col_ref, col_info = st.columns([1, 3])
with col_ref:
    if st.button("🔄 Refresh now"):
        st.rerun()
with col_info:
    st.caption(f"Last refreshed: {datetime.now().strftime('%H:%M:%S')} · "
               f"Engine cycle: {10}s · Workers: 4 parallel")

# Auto-refresh every 8 seconds
st.markdown("""
<script>
setTimeout(function(){ window.location.reload(); }, 8000);
</script>
""", unsafe_allow_html=True)
