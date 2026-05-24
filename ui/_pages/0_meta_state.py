"""
UI Page [0] — Meta-State Dynamics
Live view of the engine's ephemeral activation state (Node [M]):
  mood · top active concepts · activation pool size · decay health
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
from datetime import datetime, timezone

from meta_state import MetaState, MOOD_META

# ── Design system ─────────────────────────────────────────────────────────────

st.markdown("""
<style>
#MainMenu, footer, header[data-testid="stHeader"] { display: none !important; }
body, .stApp { background: #070709 !important; }
.block-container { padding: 1.5rem 2rem 3rem !important; max-width: 100% !important; }
*, *::before, *::after { font-family: 'Consolas', 'Courier New', monospace !important; }
[data-testid="stExpanderToggleIcon"],
[data-testid="stExpanderToggleIcon"] *,
[data-testid="stExpanderToggleIcon"] span,
[data-baseweb="icon"] span,
.material-icons, .material-symbols-rounded, .material-symbols-outlined,
span[class*="material-symbols"], span[class*="material-icons"],
[class*="MaterialIcon"], [class*="materialIcon"] {
    font-family: 'Material Symbols Rounded','Material Symbols Outlined','Material Icons',sans-serif !important;
    font-weight: normal !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-smoothing: antialiased !important;
}
h2, h3 {
    font-size: 12px !important; letter-spacing: 0.2em !important;
    text-transform: uppercase !important; color: #505078 !important;
    font-weight: 400 !important; border-bottom: 1px solid #222238 !important;
    padding-bottom: 6px !important; margin-top: 24px !important;
}
p, .stMarkdown p { color: #9898c8 !important; font-size: 14px !important; }
caption, .stCaption { color: #505078 !important; font-size: 12px !important; letter-spacing: 0.06em !important; }
hr { border-color: #222238 !important; margin: 20px 0 !important; }
[data-testid="stExpander"] { border: 1px solid #1a1a28 !important; background: #0a0a10 !important; }
[data-testid="stExpanderDetails"] { background: #07070a !important; }
summary[data-testid="stExpanderToggle"] span { color: #6868a0 !important; font-size: 13px !important; letter-spacing: 0.06em !important; }
[data-testid="stAlert"] { background: #0a0a12 !important; border-color: #222238 !important; }
</style>
""", unsafe_allow_html=True)

REFRESH_SEC = 30
st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_SEC}">', unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="padding:16px 0 4px;border-bottom:1px solid #222238;margin-bottom:20px">
  <div style="font-size:22px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;font-weight:400">META-STATE</div>
  <div style="font-size:12px;color:#505078;letter-spacing:0.06em;margin-top:4px">concept activation layer · lazy time-based decay</div>
</div>
""", unsafe_allow_html=True)

ms   = MetaState.get()
snap = ms.snapshot()

if snap.get('pool_size', 0) == 0:
    st.markdown('<div style="color:#6868a0;font-size:13px;padding:12px 0">No activations yet — run the learner to initialise meta-state.</div>', unsafe_allow_html=True)
    st.stop()

# ── Mood banner ───────────────────────────────────────────────────────────────

mood      = snap.get('mood', 'exploring')
intensity = snap.get('mood_intensity', 0.5)
_, colour, desc = MOOD_META.get(mood, ('', '#AB47BC', mood))

st.markdown(f"""
<div style="background:{colour}18;border-left:3px solid {colour};padding:14px 20px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:12px">
    <div>
      <div style="font-size:18px;letter-spacing:0.15em;color:{colour};text-transform:uppercase">{mood}</div>
      <div style="font-size:12px;color:#7878a8;margin-top:2px">{desc}</div>
    </div>
    <div style="margin-left:auto;text-align:right;font-size:12px;color:#505078">
      <div>intensity <span style="color:#9898c8">{int(intensity*100)}%</span></div>
      <div>active <span style="color:#9898c8">{snap['active_count']}</span></div>
      <div>pool <span style="color:#9898c8">{snap['pool_size']}</span></div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

ts_str = snap.get('timestamp', '')[:19].replace('T', ' ') + ' UTC'
st.markdown(f'<div style="font-size:12px;color:#404060;margin-bottom:8px">snapshot {ts_str} · auto-refresh {REFRESH_SEC}s</div>', unsafe_allow_html=True)

st.divider()

# ── Top concepts ──────────────────────────────────────────────────────────────

st.markdown("### Concept Activation")
st.markdown('<div style="font-size:12px;color:#505078;margin:-8px 0 14px">activation = stored value × decay_rate ^ elapsed_minutes</div>', unsafe_allow_html=True)

top = snap.get('top', [])
if top:
    max_v = top[0]['activation'] if top else 1.0
    for item in top:
        concept = item['concept']
        val     = item['activation']
        frac    = val / max(max_v, 0.001)
        bar_col = '#ef4444' if frac > 0.75 else '#f59e0b' if frac > 0.40 else '#4a9eff'
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:12px;padding:4px 0;font-size:13px">
          <div style="min-width:160px;color:#9898c8">{concept}</div>
          <div style="flex:1;height:8px;background:#0a0a12;border-radius:2px;overflow:hidden">
            <div style="height:100%;width:{int(frac*100)}%;background:{bar_col};border-radius:2px"></div>
          </div>
          <div style="min-width:60px;text-align:right;color:#6868a0">{val:.4f}</div>
        </div>
        """, unsafe_allow_html=True)
else:
    st.markdown('<div style="color:#505078;font-size:13px">No active concepts above threshold.</div>', unsafe_allow_html=True)

st.divider()

# ── Active dict ───────────────────────────────────────────────────────────────

active = snap.get('active', {})
if active:
    with st.expander(f"Active concepts ({len(active)}) above threshold"):
        st.json(active)

# ── Developer tools ───────────────────────────────────────────────────────────

with st.expander("Developer tools"):
    st.markdown(
        f'<div style="font-size:13px;color:#6868a0">'
        f'decay_rate <span style="color:#9898c8">{ms._decay_rate}</span> &nbsp;·&nbsp; '
        f'gain <span style="color:#9898c8">{ms._reinforce_gain}</span> &nbsp;·&nbsp; '
        f'threshold <span style="color:#9898c8">{ms._active_threshold}</span> &nbsp;·&nbsp; '
        f'cap <span style="color:#9898c8">{ms._max_concepts}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if st.button("Force decay_to(now)"):
        ms.decay_to()
        st.rerun()
    if st.button("Full snapshot JSON"):
        st.json(snap)
