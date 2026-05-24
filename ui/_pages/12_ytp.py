"""
YTP — Your Tuning Panel
All live-editable engine parameters in one place.
Writes to data/engine_config.json — engine picks up changes within one cycle (~10s).
No engine code is touched here.
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
[data-testid="stExpanderToggleIcon"] span,
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

/* Slider track */
[data-testid="stSlider"] > div > div > div { background: #222238 !important; }
[data-testid="stSlider"] > div > div > div > div { background: #4a9eff !important; }

/* Number input */
input[type="number"] {
    background: #0a0a12 !important; border: 1px solid #1a1a28 !important;
    color: #9898c8 !important; font-size: 13px !important;
}

/* Section card */
.ytp-card {
    background: #080810; border: 1px solid #181828;
    border-radius: 4px; padding: 18px 22px; margin-bottom: 16px;
}
.ytp-card-title {
    font-size: 11px; letter-spacing: 0.22em; text-transform: uppercase;
    color: #4a9eff; margin-bottom: 14px; padding-bottom: 6px;
    border-bottom: 1px solid #181828;
}
.ytp-hint {
    font-size: 11px; color: #303050; margin-top: 4px; letter-spacing: 0.04em;
}
</style>
""", unsafe_allow_html=True)

# ── Local-only gate ───────────────────────────────────────────────────────────
# Cloudflare rewrites Host → localhost but always injects CF-Ray / X-Forwarded-For.
# Deny if any proxy header is present, even if Host looks local.

try:
    _h        = st.context.headers
    _host     = _h.get("host", "")
    _proxied  = any([
        _h.get("cf-ray"),
        _h.get("cf-connecting-ip"),
        _h.get("x-forwarded-for"),
        _h.get("x-forwarded-host"),
    ])
    _is_local = (
        (_host.startswith("localhost") or _host.startswith("127.0.0.1"))
        and not _proxied
    )
except Exception:
    _is_local = False  # deny on any error

if not _is_local:
    st.markdown("""
    <div style="padding:60px 40px;text-align:center">
      <div style="font-size:14px;letter-spacing:0.2em;text-transform:uppercase;color:#ff4444">access denied</div>
      <div style="font-size:12px;color:#404060;margin-top:12px;letter-spacing:0.06em">
        YTP is only available on local access
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="padding:16px 0 4px;border-bottom:1px solid #222238;margin-bottom:20px">
  <div style="font-size:22px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;font-weight:400">YTP</div>
  <div style="font-size:12px;color:#505078;letter-spacing:0.06em;margin-top:4px">
    your tuning panel · all parameters · changes apply within one learner cycle (~10s)
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

def _g(section, key, default):
    return cfg.get(section, {}).get(key, default)

def _s(section, key, value):
    cfg.setdefault(section, {})[key] = value

# ══════════════════════════════════════════════════════════════════════════════
# LEARNING SPEED
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Learning Speed")
st.markdown('<div class="ytp-hint">Controls how fast and how deep the engine reads each cycle.</div>', unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)

with c1:
    v = st.slider("Cycle time (s)", 5, 120, int(_g("learning","cycle_time",10)),
                  help="Seconds between learning cycles. Lower = faster but more CPU.")
    _s("learning","cycle_time", v)

    v = st.slider("Topics per cycle", 1, 50, int(_g("learning","topics_per_cycle",12)),
                  help="How many topics to search each cycle.")
    _s("learning","topics_per_cycle", v)

with c2:
    v = st.slider("Parallel workers", 1, 20, int(_g("learning","n_workers",8)),
                  help="Thread pool size. More workers = faster but more RAM/CPU.")
    _s("learning","n_workers", v)

    v = st.slider("Min sentences", 1, 20, int(_g("learning","min_sentences",3)),
                  help="Skip content with fewer sentences than this.")
    _s("learning","min_sentences", v)

with c3:
    v = st.slider("Fetch timeout (s)", 3, 60, int(_g("learning","fetch_timeout",12)),
                  help="How long to wait when downloading content.")
    _s("learning","fetch_timeout", v)

    v = st.slider("Search timeout (s)", 3, 30, int(_g("learning","search_timeout",7)),
                  help="How long to wait for search API responses.")
    _s("learning","search_timeout", v)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# GOAL DRIVES
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Goal Drives")
st.markdown('<div class="ytp-hint">Weights for the five intrinsic drives. Higher = more influence on what the engine pursues.</div>', unsafe_allow_html=True)

_drives = {
    "reduce_uncertainty":    ("Reduce Uncertainty",    0.30, "Engine seeks to clarify ambiguous concepts."),
    "increase_novelty":      ("Increase Novelty",      0.25, "Engine explores new unfamiliar territory."),
    "resolve_contradiction": ("Resolve Contradiction", 0.20, "Engine focuses on conflicting beliefs."),
    "maintain_stability":    ("Maintain Stability",    0.15, "Engine reinforces what it already knows well."),
    "expand_regions":        ("Expand Regions",        0.10, "Engine grows into new topic regions."),
}

g1, g2 = st.columns(2)
_drive_keys = list(_drives.keys())
for i, k in enumerate(_drive_keys):
    label, default, hint = _drives[k]
    col = g1 if i % 2 == 0 else g2
    with col:
        v = st.slider(label, 0.0, 1.0, float(_g("goals", k, default)), 0.05, key="goal_"+k)
        _s("goals", k, v)
        st.markdown(f'<div class="ytp-hint">{hint}</div>', unsafe_allow_html=True)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# ENERGY BUDGET
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Energy Budget")
st.markdown('<div class="ytp-hint">The engine has a finite energy pool [0–1]. High cost = rarer actions. Low replenish = engine runs out faster.</div>', unsafe_allow_html=True)

e1, e2 = st.columns(2)

with e1:
    v = st.slider("Replenish per tick", 0.01, 0.30, float(_g("energy","replenish_per_tick",0.08)), 0.01,
                  help="Energy restored each cycle. Higher = more active.")
    _s("energy","replenish_per_tick", v)

    v = st.slider("Cost: exploration", 0.01, 0.30, float(_g("energy","cost_exploration",0.06)), 0.01)
    _s("energy","cost_exploration", v)

    v = st.slider("Cost: simulation step", 0.01, 0.25, float(_g("energy","cost_simulation",0.04)), 0.01)
    _s("energy","cost_simulation", v)

with e2:
    v = st.slider("Cost: region switch", 0.01, 0.25, float(_g("energy","cost_region_switch",0.05)), 0.01)
    _s("energy","cost_region_switch", v)

    v = st.slider("Cost: abstraction run", 0.01, 0.30, float(_g("energy","cost_abstraction",0.10)), 0.01)
    _s("energy","cost_abstraction", v)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# ATTENTION
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Attention")
st.markdown('<div class="ytp-hint">Controls how strongly the engine biases towards familiar vs novel concepts.</div>', unsafe_allow_html=True)

a1, a2 = st.columns(2)

with a1:
    v = st.slider("Bias strength", 0.0, 1.0, float(_g("attention","bias_strength",0.0)), 0.05,
                  help="How strongly existing concepts bias new attention.")
    _s("attention","bias_strength", v)

with a2:
    v = st.slider("Novelty strength", 0.0, 1.0, float(_g("attention","novelty_strength",0.0)), 0.05,
                  help="How strongly novel concepts attract attention.")
    _s("attention","novelty_strength", v)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# PERSONALITY — META-STATE
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Personality — Concept Activation")
st.markdown('<div class="ytp-hint">Controls how concepts fade and reinforce in the attention pool (meta-state).</div>', unsafe_allow_html=True)

m1, m2 = st.columns(2)

with m1:
    v = st.slider("Decay rate", 0.90, 0.999, float(_g("meta_state","decay_rate",0.97)), 0.001, format="%.3f",
                  help="How fast concepts fade per minute. Higher = slower forgetting.")
    _s("meta_state","decay_rate", v)

    v = st.slider("Reinforce gain", 0.05, 0.80, float(_g("meta_state","reinforce_gain",0.25)), 0.05,
                  help="Boost applied when a concept is re-encountered.")
    _s("meta_state","reinforce_gain", v)

with m2:
    v = st.slider("Fatigue (repetition penalty)", 0.0, 1.0, float(_g("meta_state","fatigue_k",0.2)), 0.05,
                  help="Reduces gain for concepts seen too frequently.")
    _s("meta_state","fatigue_k", v)

    v = st.slider("Cooling alpha", 0.0, 1.0, float(_g("meta_state","cooling_alpha",0.5)), 0.05,
                  help="Blend between raw and cooled activation. Higher = more cooling.")
    _s("meta_state","cooling_alpha", v)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# PERSONALITY — IDENTITY & EVOLVER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### Personality — Identity & Evolver")
st.markdown('<div class="ytp-hint">Controls how slowly the engine\'s personality traits drift and self-adapt over time.</div>', unsafe_allow_html=True)

i1, i2 = st.columns(2)

with i1:
    v = st.slider("Identity drift rate", 0.001, 0.10, float(_g("identity","drift_rate",0.02)), 0.001, format="%.3f",
                  help="How fast personality traits drift each observation cycle.")
    _s("identity","drift_rate", v)

with i2:
    v = st.slider("Evolver max delta", 0.01, 0.20, float(_g("evolver","max_delta",0.05)), 0.01,
                  help="Max change per hill-climbing adaptation step.")
    _s("evolver","max_delta", v)

    v = st.number_input("Adapt every N ticks", 5, 200, int(_g("evolver","adapt_every",30)),
                        help="How many cycles between evolver adaptation steps.")
    _s("evolver","adapt_every", v)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SAVE / RESET
# ══════════════════════════════════════════════════════════════════════════════

sv_col, rs_col, _ = st.columns([1, 1, 2])

with sv_col:
    if st.button("Apply All", type="primary", width='stretch'):
        _save(cfg)
        st.success("Saved. Active within next learner cycle.")

with rs_col:
    if st.button("Reset Defaults", width='stretch'):
        egos_bak      = cfg.get("egos", {})
        active_bak    = cfg.get("active_ego", "")
        defaults = {
            "learning":   {"cycle_time":10,"n_workers":8,"topics_per_cycle":12,
                           "min_sentences":3,"fetch_timeout":12,"search_timeout":7,
                           "checkin_every":10800,"feed_max":200},
            "energy":     {"total":1.0,"replenish_per_tick":0.08,"cost_simulation":0.04,
                           "cost_exploration":0.06,"cost_region_switch":0.05,"cost_abstraction":0.10},
            "identity":   {"drift_rate":0.02},
            "evolver":    {"adapt_every":30,"max_delta":0.05},
            "attention":  {"bias_strength":0.0,"novelty_strength":0.0},
            "meta_state": {"decay_rate":0.97,"reinforce_gain":0.25,"fatigue_k":0.2,"cooling_alpha":0.5},
            "goals":      {"reduce_uncertainty":0.30,"increase_novelty":0.25,
                           "resolve_contradiction":0.20,"maintain_stability":0.15,"expand_regions":0.10},
            "egos":       egos_bak,
            "active_ego": active_bak,
        }
        _save(defaults)
        st.success("Reset to defaults.")
        st.rerun()
