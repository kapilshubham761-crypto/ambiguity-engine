"""
YTP — Your Tuning Panel
Variables are split into three layers:
  Layer 1 — Physics (manual sliders) : energy capacity, safety bounds
  Layer 2 — Regulated (auto)         : all cognitive parameters, shown live
  Layer 3 — Personality (slow drift) : handled by IdentityTracker / Evolver

Writes to data/engine_config.json — engine picks up changes within one cycle.
"""
import sys, json
from pathlib import Path
import streamlit as st

_ROOT     = Path(__file__).parent.parent.parent
_CFG_PATH = _ROOT / "data" / "engine_config.json"

sys.path.insert(0, str(_ROOT / "src"))

# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
#MainMenu, footer, header[data-testid="stHeader"] { display: none !important; }
body, .stApp { background: #070709 !important; }
.block-container { padding: 1.5rem 2rem 3rem !important; max-width: 100% !important; }
*, *::before, *::after { font-family: 'Consolas', 'Courier New', monospace !important; }
[data-testid="stExpanderToggleIcon"],
[data-testid="stExpanderToggleIcon"] *,
[data-baseweb="icon"] span,
.material-icons, .material-symbols-rounded, .material-symbols-outlined,
span[class*="material-symbols"], span[class*="material-icons"],
[class*="MaterialIcon"], [class*="materialIcon"] {
    font-family: 'Material Symbols Rounded','Material Symbols Outlined','Material Icons',sans-serif !important;
    font-weight: normal !important; letter-spacing: normal !important;
    text-transform: none !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-smoothing: antialiased !important;
}
h2, h3 {
    font-size: 11px !important; letter-spacing: 0.22em !important;
    text-transform: uppercase !important; color: #404060 !important;
    font-weight: 400 !important; border-bottom: 1px solid #181828 !important;
    padding-bottom: 5px !important; margin-top: 28px !important;
}
p, .stMarkdown p { color: #9898c8 !important; font-size: 13px !important; }
caption, .stCaption { color: #404060 !important; font-size: 11px !important; letter-spacing: 0.06em !important; }
hr { border-color: #181828 !important; margin: 16px 0 !important; }
label { color: #6060a0 !important; font-size: 11px !important; letter-spacing: 0.1em !important; text-transform: uppercase !important; }
[data-testid="stSlider"] > div > div > div { background: #222238 !important; }
[data-testid="stSlider"] > div > div > div > div { background: #4a9eff !important; }
input[type="number"] {
    background: #0a0a12 !important; border: 1px solid #1a1a28 !important;
    color: #9898c8 !important; font-size: 13px !important;
}
.reg-row {
    display: flex; align-items: center; gap: 12px;
    padding: 7px 0; border-bottom: 1px solid #0f0f1a;
}
.reg-label { font-size: 11px; color: #505078; letter-spacing: 0.1em; text-transform: uppercase; min-width: 200px; }
.reg-bar-bg { flex: 1; height: 4px; background: #111120; border-radius: 2px; }
.reg-bar-fill { height: 4px; border-radius: 2px; background: #4a9eff; }
.reg-val { font-size: 12px; color: #9898c8; min-width: 52px; text-align: right; }
.reg-badge { font-size: 9px; letter-spacing: 0.15em; color: #44ff88; border: 1px solid #1a3a1a;
             background: #0a1a0a; padding: 1px 5px; border-radius: 2px; }
.layer-title {
    font-size: 10px; letter-spacing: 0.25em; text-transform: uppercase;
    padding: 10px 0 6px; margin-top: 24px; border-bottom: 1px solid #181828;
}
</style>
""", unsafe_allow_html=True)

# ── Local-only gate ───────────────────────────────────────────────────────────

try:
    _h        = st.context.headers
    _host     = _h.get("host", "")
    _proxied  = any([_h.get("cf-ray"), _h.get("cf-connecting-ip"),
                     _h.get("x-forwarded-for"), _h.get("x-forwarded-host")])
    _is_local = (_host.startswith("localhost") or _host.startswith("127.0.0.1")) and not _proxied
except Exception:
    _is_local = False

if not _is_local:
    st.markdown("""
    <div style="padding:60px 40px;text-align:center">
      <div style="font-size:14px;letter-spacing:0.2em;text-transform:uppercase;color:#ff4444">access denied</div>
      <div style="font-size:12px;color:#404060;margin-top:12px;letter-spacing:0.06em">YTP is only available on local access</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="padding:16px 0 4px;border-bottom:1px solid #222238;margin-bottom:20px">
  <div style="font-size:22px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;font-weight:400">YTP</div>
  <div style="font-size:12px;color:#505078;letter-spacing:0.06em;margin-top:4px">
    your tuning panel · layer 1 = physics (manual) · layer 2 = regulated (autonomous)
  </div>
</div>
""", unsafe_allow_html=True)

# ── Load / save helpers ───────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save(cfg: dict) -> None:
    tmp = _CFG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(_CFG_PATH)
    try:
        from config import Config
        Config.get_instance()._mtime = 0.0
    except Exception:
        pass

cfg = _load()
regulated = cfg.get("_regulated", False)

def _g(section, key, default):
    return cfg.get(section, {}).get(key, default)

def _s(section, key, value):
    cfg.setdefault(section, {})[key] = value

# ── Helper: render a regulated variable row ───────────────────────────────────

def _reg_row(label: str, value: float, lo: float = 0.0, hi: float = 1.0, fmt: str = ".3f"):
    pct = int((value - lo) / max(hi - lo, 1e-6) * 100)
    pct = max(0, min(100, pct))
    st.markdown(f"""
    <div class="reg-row">
      <div class="reg-label">{label}</div>
      <div class="reg-bar-bg"><div class="reg-bar-fill" style="width:{pct}%"></div></div>
      <div class="reg-val">{value:{fmt}}</div>
      <div class="reg-badge">AUTO</div>
    </div>
    """, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — PHYSICS (manual sliders)
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="layer-title" style="color:#4a9eff">Layer 1 — Physics Constants <span style="color:#303050;font-size:9px;margin-left:8px">manual · rarely changed</span></div>', unsafe_allow_html=True)

e1, e2 = st.columns(2)
with e1:
    v = st.slider("Energy replenish / tick", 0.01, 0.30, float(_g("energy","replenish_per_tick",0.08)), 0.01)
    _s("energy","replenish_per_tick", v)
    v = st.slider("Cost — exploration",      0.01, 0.30, float(_g("energy","cost_exploration",0.06)), 0.01)
    _s("energy","cost_exploration", v)
    v = st.slider("Cost — simulation",       0.01, 0.25, float(_g("energy","cost_simulation",0.04)), 0.01)
    _s("energy","cost_simulation", v)

with e2:
    v = st.slider("Cost — region switch",    0.01, 0.25, float(_g("energy","cost_region_switch",0.05)), 0.01)
    _s("energy","cost_region_switch", v)
    v = st.slider("Cost — abstraction",      0.01, 0.30, float(_g("energy","cost_abstraction",0.10)), 0.01)
    _s("energy","cost_abstraction", v)
    v = st.slider("Evolver max delta",        0.01, 0.20, float(_g("evolver","max_delta",0.05)), 0.01)
    _s("evolver","max_delta", v)

sv_col, rs_col, _ = st.columns([1, 1, 3])
with sv_col:
    if st.button("Apply Physics", type="primary", width="stretch"):
        _save(cfg)
        st.success("Saved.")
with rs_col:
    if st.button("Reset Physics", width="stretch"):
        for sec, k, dv in [
            ("energy","replenish_per_tick",0.08), ("energy","cost_exploration",0.06),
            ("energy","cost_simulation",0.04),    ("energy","cost_region_switch",0.05),
            ("energy","cost_abstraction",0.10),   ("evolver","max_delta",0.05),
        ]:
            _s(sec, k, dv)
        _save(cfg)
        st.success("Reset.")
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — REGULATED (live read-only display)
# ══════════════════════════════════════════════════════════════════════════════

st.divider()
st.markdown('<div class="layer-title" style="color:#44ff88">Layer 2 — Adaptive Regulators <span style="color:#303050;font-size:9px;margin-left:8px">autonomous · updated every 60s by MetaRegulator</span></div>', unsafe_allow_html=True)

if not regulated:
    st.markdown('<div style="color:#303050;font-size:12px;padding:12px 0">Regulator has not run yet — starts 60s after engine launch.</div>', unsafe_allow_html=True)
else:
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown('<div style="font-size:10px;color:#404060;letter-spacing:0.15em;margin-bottom:8px">ATTENTION</div>', unsafe_allow_html=True)
        _reg_row("Novelty strength",  float(_g("attention","novelty_strength",0.0)))
        _reg_row("Bias strength",     float(_g("attention","bias_strength",0.0)))

        st.markdown('<div style="font-size:10px;color:#404060;letter-spacing:0.15em;margin:16px 0 8px">META-STATE</div>', unsafe_allow_html=True)
        _reg_row("Decay rate",        float(_g("meta_state","decay_rate",0.97)),       0.92,  0.999)
        _reg_row("Reinforce gain",    float(_g("meta_state","reinforce_gain",0.25)),    0.05,  0.70)
        _reg_row("Fatigue",           float(_g("meta_state","fatigue_k",0.2)),          0.05,  0.85)
        _reg_row("Cooling alpha",     float(_g("meta_state","cooling_alpha",0.5)))

        st.markdown('<div style="font-size:10px;color:#404060;letter-spacing:0.15em;margin:16px 0 8px">IDENTITY</div>', unsafe_allow_html=True)
        _reg_row("Drift rate",        float(_g("identity","drift_rate",0.02)),          0.001, 0.08)

    with col_b:
        st.markdown('<div style="font-size:10px;color:#404060;letter-spacing:0.15em;margin-bottom:8px">GOAL DRIVES</div>', unsafe_allow_html=True)
        _reg_row("Reduce uncertainty",    float(_g("goals","reduce_uncertainty",0.30)))
        _reg_row("Increase novelty",      float(_g("goals","increase_novelty",0.25)))
        _reg_row("Resolve contradiction", float(_g("goals","resolve_contradiction",0.20)))
        _reg_row("Maintain stability",    float(_g("goals","maintain_stability",0.15)))
        _reg_row("Expand regions",        float(_g("goals","expand_regions",0.10)))

# ══════════════════════════════════════════════════════════════════════════════
# RAW CONFIG
# ══════════════════════════════════════════════════════════════════════════════

st.divider()
with st.expander("Raw engine_config.json"):
    try:
        st.json(json.loads(_CFG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        st.error(str(e))
