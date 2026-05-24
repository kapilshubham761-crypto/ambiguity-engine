"""
UI Page [0] — Field State
Live view of the JAM field: top active concepts, mode/goal, field stats.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import streamlit as st
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

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
[data-testid="stMetric"] { background: #0a0a12; border: 1px solid #1a1a28; border-radius: 4px; padding: 10px 14px; }
[data-testid="stMetricLabel"] p { color: #505078 !important; font-size: 11px !important; letter-spacing: 0.15em !important; text-transform: uppercase !important; }
[data-testid="stMetricValue"] { color: #9898c8 !important; font-size: 28px !important; }
</style>
""", unsafe_allow_html=True)

REFRESH_SEC = 10

st.markdown("""
<div style="padding:16px 0 4px;border-bottom:1px solid #222238;margin-bottom:20px">
  <div style="font-size:22px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;font-weight:400">FIELD STATE</div>
  <div style="font-size:12px;color:#505078;letter-spacing:0.06em;margin-top:4px">jam field · concept activation · dynamics</div>
</div>
""", unsafe_allow_html=True)

# ── Load data ─────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}

field_snap = _load(ROOT / 'data' / 'jam_field.json')
reg_snap   = _load(ROOT / 'data' / 'regulation.json')
cog_snap   = _load(ROOT / 'data' / 'cog_status.json')

if not field_snap:
    st.markdown('<div style="color:#6868a0;font-size:13px;padding:12px 0">No field data yet — learner initialising.</div>', unsafe_allow_html=True)
    st.stop()

# ── Mode banner ───────────────────────────────────────────────────────────────

mode = cog_snap.get('mode', reg_snap.get('mode', 'associative'))
goal = cog_snap.get('goal', reg_snap.get('goal', 'explore')).replace('_', ' ')

_MODE_COLOUR = {
    'focused':      '#f59e0b',
    'exploratory':  '#3b82f6',
    'conflicted':   '#ef4444',
    'saturated':    '#8b5cf6',
    'drifting':     '#10b981',
    'associative':  '#4a9eff',
}
mc = _MODE_COLOUR.get(mode, '#6b7280')

entropy  = reg_snap.get('entropy', 0.0)
pressure = reg_snap.get('pressure', 0.0)

st.markdown(f"""
<div style="background:{mc}18;border-left:3px solid {mc};padding:14px 20px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:12px">
    <div>
      <div style="font-size:18px;letter-spacing:0.15em;color:{mc};text-transform:uppercase">{mode}</div>
      <div style="font-size:12px;color:#7878a8;margin-top:2px">drive: {goal}</div>
    </div>
    <div style="margin-left:auto;text-align:right;font-size:12px;color:#505078">
      <div>entropy <span style="color:#9898c8">{entropy:.3f}</span></div>
      <div>pressure <span style="color:#9898c8">{pressure:.3f}</span></div>
      <div>active <span style="color:#9898c8">{field_snap.get('active_count', 0)}</span></div>
      <div>field size <span style="color:#9898c8">{field_snap.get('field_size', 0)}</span></div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

st.divider()

# ── Regulation scalars ────────────────────────────────────────────────────────

st.markdown("### Regulation Scalars")

c1, c2, c3 = st.columns(3)
c1.metric("Gain Rate",        f"{reg_snap.get('gain_rate', 0):.4f}")
c2.metric("Decay Rate",       f"{reg_snap.get('decay_rate', 0):.5f}")
c3.metric("Diffusion",        f"{reg_snap.get('diffusion_strength', 0):.4f}")

st.divider()

# ── Top active concepts ───────────────────────────────────────────────────────

st.markdown("### Concept Activation")
st.markdown('<div style="font-size:12px;color:#505078;margin:-8px 0 14px">live activation · lazy time-based decay</div>', unsafe_allow_html=True)

top = field_snap.get('top', [])
if top:
    max_v = top[0][1] if top else 1.0
    for text, val in top:
        frac    = val / max(float(max_v), 0.001)
        bar_col = '#ef4444' if frac > 0.75 else '#f59e0b' if frac > 0.40 else '#4a9eff'
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:12px;padding:4px 0;font-size:13px">
          <div style="min-width:200px;color:#9898c8">{text}</div>
          <div style="flex:1;height:8px;background:#0a0a12;border-radius:2px;overflow:hidden">
            <div style="height:100%;width:{int(frac*100)}%;background:{bar_col};border-radius:2px"></div>
          </div>
          <div style="min-width:60px;text-align:right;color:#6868a0">{val:.5f}</div>
        </div>
        """, unsafe_allow_html=True)
else:
    st.markdown('<div style="color:#505078;font-size:13px">No active concepts above threshold.</div>', unsafe_allow_html=True)

st.divider()

# ── Field stats ───────────────────────────────────────────────────────────────

stats = field_snap.get('field_stats', {})
if stats:
    st.markdown("### Field Statistics")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Mean Activation", f"{stats.get('mean_activation', 0):.4f}")
    s2.metric("Mean Novelty",    f"{stats.get('mean_novelty', 0):.4f}")
    s3.metric("Mean Tension",    f"{stats.get('mean_tension', 0):.4f}")
    s4.metric("Mean Stability",  f"{stats.get('mean_stability', 0):.4f}")

st.caption(f"auto-refresh {REFRESH_SEC}s · tick {reg_snap.get('tick_count', 0)}")

import time as _time
_time.sleep(REFRESH_SEC)
st.rerun()
