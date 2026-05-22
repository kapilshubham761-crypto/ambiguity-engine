"""
UI Page [0] — Meta-State Dynamics
Node position: ui/_pages/0_meta_state.py  →  see ARCHITECTURE.md

Live view of the engine's ephemeral activation state (Node [M]):
  mood · top active concepts · activation pool size · decay health
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
from datetime import datetime, timezone

from meta_state import MetaState, MOOD_META

st.set_page_config(page_title="Meta-State", page_icon="🧠", layout="wide")
st.title("🧠 Meta-State Dynamics")
st.caption("Concept activation layer — lazy time-based decay · Node [M]")

REFRESH_SEC = 30
st.markdown(
    f'<meta http-equiv="refresh" content="{REFRESH_SEC}">',
    unsafe_allow_html=True,
)

ms   = MetaState.get()
snap = ms.snapshot()

# ── Empty state ───────────────────────────────────────────────────────────────
if snap.get('pool_size', 0) == 0:
    st.info("No activations yet — accept at least one lesson to initialise meta-state.")
    st.stop()

# ── Mood banner ───────────────────────────────────────────────────────────────
mood      = snap.get('mood', 'exploring')
intensity = snap.get('mood_intensity', 0.5)
emoji, colour, desc = MOOD_META.get(mood, ('🧭', '#AB47BC', mood))

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
      <span style="font-size:1.6rem;font-weight:700;margin-left:10px">{mood.upper()}</span>
      <span style="color:{colour};margin-left:12px;font-size:0.95rem">{desc}</span>
      <br>
      <span style="font-size:0.8rem;color:#888">
        Intensity {int(intensity*100)}% &nbsp;·&nbsp;
        {snap['active_count']} active concepts &nbsp;·&nbsp;
        pool {snap['pool_size']} entries
      </span>
    </div>
    """,
    unsafe_allow_html=True,
)

ts_str = snap.get('timestamp', '')[:19].replace('T', ' ') + ' UTC'
st.caption(f"Snapshot: {ts_str} · auto-refresh every {REFRESH_SEC}s")

st.divider()

# ── Top concepts ──────────────────────────────────────────────────────────────
st.subheader("🏃 Concept Activation")
st.caption("Current activation = stored value × decay_rate ^ elapsed_minutes.")

top = snap.get('top', [])
if top:
    max_v = top[0]['activation'] if top else 1.0
    for item in top:
        concept = item['concept']
        val     = item['activation']
        frac    = val / max(max_v, 0.001)

        if frac > 0.75:
            bar_colour = '#FF7043'
        elif frac > 0.40:
            bar_colour = '#FFA726'
        else:
            bar_colour = '#4FC3F7'

        col_name, col_bar = st.columns([2, 5])
        col_name.markdown(f"`{concept}`")
        col_bar.markdown(
            f'<div style="background:{bar_colour};height:18px;width:{int(frac*100)}%;'
            f'border-radius:4px;display:inline-block"></div> '
            f'<small>{val:.4f}</small>',
            unsafe_allow_html=True,
        )
else:
    st.info("No active concepts above threshold.")

st.divider()

# ── Active dict (above threshold) ────────────────────────────────────────────
active = snap.get('active', {})
if active:
    with st.expander(f"Active concepts ({len(active)}) above threshold"):
        st.json(active)

# ── Developer tools ───────────────────────────────────────────────────────────
with st.expander("Developer tools"):
    cfg_col, btn_col = st.columns([3, 1])
    cfg_col.markdown(
        f"**decay_rate** `{ms._decay_rate}` · "
        f"**gain** `{ms._reinforce_gain}` · "
        f"**threshold** `{ms._active_threshold}` · "
        f"**cap** `{ms._max_concepts}`"
    )
    if btn_col.button("Force decay_to(now)"):
        ms.decay_to()
        st.rerun()

    if st.button("Full snapshot JSON"):
        st.json(snap)
