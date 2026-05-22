"""
UI Page [0] — Meta-State Dynamics
Node position: ui/_pages/0_meta_state.py  →  see ARCHITECTURE.md

Live view of the engine's ephemeral internal state:
  mood · tensions · attractors · context drift · semantic momentum
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import json
import streamlit as st
from datetime import datetime, timezone

from graph import SemanticGraph
from teacher import Teacher
from meta_state import MetaStateEngine, MOOD_META

st.set_page_config(page_title="Meta-State", page_icon="🧠", layout="wide")
st.title("🧠 Meta-State Dynamics")
st.caption("Ephemeral internal state — mood, tensions, attractors, drift, momentum.")

# ── Auto-refresh ──────────────────────────────────────────────────────────────
REFRESH_SEC = 30
st.markdown(
    f'<meta http-equiv="refresh" content="{REFRESH_SEC}">',
    unsafe_allow_html=True,
)

# ── Singletons ────────────────────────────────────────────────────────────────
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
eng = MetaStateEngine.get()
ms  = eng.current()

# ── Empty state ───────────────────────────────────────────────────────────────
if ms is None:
    st.info("No meta-state yet — accept at least one lesson to initialise the engine.")
    st.stop()

# ── Mood banner ───────────────────────────────────────────────────────────────
emoji, colour, desc = MOOD_META.get(ms.mood, ('🧭', '#AB47BC', ms.mood))
intensity_pct = int(ms.mood_intensity * 100)

st.markdown(
    f"""
    <div style="
        background:{colour}22;
        border-left:6px solid {colour};
        padding:16px 20px;
        border-radius:8px;
        margin-bottom:12px;
    ">
      <span style="font-size:2rem">{emoji}</span>
      <span style="font-size:1.6rem;font-weight:700;margin-left:10px">{ms.mood.upper()}</span>
      <span style="color:{colour};margin-left:12px;font-size:0.95rem">{desc}</span>
      <br>
      <span style="font-size:0.8rem;color:#888">
        Intensity {intensity_pct}% &nbsp;·&nbsp;
        {ms.active_concept_count} active concepts &nbsp;·&nbsp;
        avg momentum {ms.avg_momentum:.3f} &nbsp;·&nbsp;
        +{ms.recent_growth} nodes since last update
      </span>
    </div>
    """,
    unsafe_allow_html=True,
)

ts_str = ms.timestamp[:19].replace('T', ' ') + ' UTC'
st.caption(f"Last updated: {ts_str} · auto-refresh every {REFRESH_SEC}s")

st.divider()

# ── Three-column overview: Tensions | Attractors | Drift ─────────────────────
col_t, col_a, col_d = st.columns(3)

with col_t:
    st.subheader("⚡ Active Tensions")
    st.caption("Concept pairs in structural conflict — associated but pulling apart.")
    if ms.tensions:
        for ten in ms.tensions:
            strength_bar = "█" * int(ten['strength'] * 20)
            st.markdown(
                f"**{ten['concept_a']}** ↔ **{ten['concept_b']}**  \n"
                f"`{strength_bar}` {ten['strength']:.3f}  "
                f"<small>edge {ten['edge_weight']:.3f}</small>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No tensions detected — concepts are integrating harmoniously.")

with col_a:
    st.subheader("🌀 Unstable Attractors")
    st.caption("High-momentum hubs pulling new concepts toward them.")
    if ms.attractors:
        max_pull = ms.attractors[0]['pull'] if ms.attractors else 1.0
        for att in ms.attractors:
            frac = att['pull'] / max(max_pull, 0.001)
            st.markdown(f"**{att['concept']}**")
            st.progress(frac, text=f"pull {att['pull']:.3f}  ·  deg {att['degree']}  ·  mom {att['momentum']:.3f}")
    else:
        st.info("No strong attractors yet.")

with col_d:
    st.subheader("🌊 Context Drift")
    st.caption("Concepts moving in and out of semantic focus.")
    if ms.drift_in or ms.drift_out:
        if ms.drift_in:
            st.markdown("**Incoming ↑**")
            for c in ms.drift_in:
                st.markdown(f"&nbsp;&nbsp;▲ `{c}`", unsafe_allow_html=True)
        if ms.drift_out:
            st.markdown("**Outgoing ↓**")
            for c in ms.drift_out:
                st.markdown(f"&nbsp;&nbsp;▽ `{c}`", unsafe_allow_html=True)
    else:
        st.info("Context stable — no significant drift.")

    if ms.context_window:
        with st.expander("Context window (last 60)"):
            st.write(" · ".join(ms.context_window[-20:]))

st.divider()

# ── Momentum chart ────────────────────────────────────────────────────────────
st.subheader("🏃 Semantic Momentum")
st.caption("Activation inertia per concept — decays by ×0.82 each lesson accepted.")

if ms.momentum:
    top = sorted(ms.momentum.items(), key=lambda x: x[1], reverse=True)[:15]
    max_mom = top[0][1] if top else 1.0

    for concept, mom in top:
        frac = mom / max(max_mom, 0.001)
        # colour by intensity
        if frac > 0.75:
            bar_colour = "#FF7043"
        elif frac > 0.40:
            bar_colour = "#FFA726"
        else:
            bar_colour = "#4FC3F7"
        col_name, col_bar = st.columns([2, 5])
        col_name.markdown(f"`{concept}`")
        col_bar.markdown(
            f'<div style="background:{bar_colour};height:18px;width:{int(frac*100)}%;'
            f'border-radius:4px;display:inline-block"></div> '
            f'<small>{mom:.3f}</small>',
            unsafe_allow_html=True,
        )
else:
    st.info("No momentum data yet.")

st.divider()

# ── Manual trigger ────────────────────────────────────────────────────────────
with st.expander("Developer tools"):
    st.caption("Force a meta-state recompute from the current graph.")
    if st.button("Recompute now"):
        # Feed empty concept list — just recomputes from existing momentum + graph
        eng.on_accept([], g)
        st.rerun()

    raw = eng.snapshot()
    if raw:
        st.json({k: v for k, v in raw.items()
                 if k not in ('momentum', 'context_window')})
