"""Settings - live-editable engine parameters."""
import sys, os, json, subprocess, re, time
from pathlib import Path
import streamlit as st

_ROOT      = Path(__file__).parent.parent.parent
_CFG_PATH  = _ROOT / "data" / "engine_config.json"
_CF_BIN    = _ROOT / "cloudflared.exe"
_CF_LOG    = _ROOT / "data" / "tunnel.log"
_CF_PID    = _ROOT / "data" / "tunnel.pid"

sys.path.insert(0, str(_ROOT / "src"))


# ── Tunnel helpers ────────────────────────────────────────────────────────────

def _tunnel_running() -> bool:
    try:
        pid = int(_CF_PID.read_text())
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False

def _tunnel_url() -> str:
    try:
        log = _CF_LOG.read_text(encoding='utf-8', errors='ignore')
        m   = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', log)
        return m.group(0) if m else ''
    except Exception:
        return ''

def _start_tunnel():
    if not _CF_BIN.exists():
        return 'cloudflared.exe not found'
    _CF_LOG.write_text('', encoding='utf-8')
    proc = subprocess.Popen(
        [str(_CF_BIN), 'tunnel', '--url', 'http://localhost:8501'],
        stdout=open(_CF_LOG, 'w'), stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    _CF_PID.write_text(str(proc.pid))
    return ''

def _stop_tunnel():
    try:
        pid = int(_CF_PID.read_text())
        import psutil, signal
        p = psutil.Process(pid)
        p.terminate()
    except Exception:
        pass
    try: _CF_PID.unlink()
    except Exception: pass

st.title("Settings")
st.caption("Changes take effect within one learner cycle (~10 s). No restart needed unless noted.")

# ── Public Access (Cloudflare Tunnel) ─────────────────────────────────────────
st.subheader("Public Access")
running = _tunnel_running()

t_col1, t_col2 = st.columns([1, 3])
with t_col1:
    if running:
        if st.button("Stop Tunnel", width='stretch'):
            _stop_tunnel()
            st.success("Tunnel stopped.")
            st.rerun()
    else:
        if st.button("Start Tunnel", type="primary", width='stretch'):
            err = _start_tunnel()
            if err:
                st.error(err)
            else:
                st.info("Starting… URL appears in ~5 seconds.")
                time.sleep(5)
                st.rerun()

with t_col2:
    if running:
        url = _tunnel_url()
        if url:
            st.success(f"Live: [{url}]({url})")
            st.caption("Share this link — anyone can access the engine from outside your network.")
        else:
            st.info("Tunnel starting — URL loading…")
            time.sleep(3)
            st.rerun()
    else:
        st.caption("Creates a free Cloudflare public URL for localhost:8501. No account needed.")

st.divider()

MAX_EGOS = 3

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

def _get(section, key, default):
    return cfg.get(section, {}).get(key, default)

def _set(section, key, value):
    cfg.setdefault(section, {})[key] = value

def _ego_snapshot(cfg: dict) -> dict:
    """Extract personality sections into an ego dict."""
    return {
        "identity":   dict(cfg.get("identity",   {})),
        "evolver":    dict(cfg.get("evolver",     {})),
        "meta_state": dict(cfg.get("meta_state",  {})),
        "goals":      dict(cfg.get("goals",       {})),
        "attention":  dict(cfg.get("attention",   {})),
    }

def _apply_ego(cfg: dict, ego: dict) -> None:
    """Load ego personality values into cfg in-place."""
    for section, vals in ego.items():
        cfg.setdefault(section, {}).update(vals)

# ── Ego selector ──────────────────────────────────────────────────────────────
st.subheader("Ego")
egos: dict = cfg.get("egos", {})
ego_names  = list(egos.keys())

ego_col1, ego_col2, ego_col3 = st.columns([2, 1, 1])

with ego_col1:
    options    = ["— none / custom —"] + ego_names
    active_ego = cfg.get("active_ego", "")
    default_i  = options.index(active_ego) if active_ego in options else 0
    selected   = st.selectbox("Load ego", options, index=default_i,
                              label_visibility="collapsed")

with ego_col2:
    if st.button("Load", width='stretch',
                 disabled=(selected == "— none / custom —")):
        _apply_ego(cfg, egos[selected])
        cfg["active_ego"] = selected
        _save(cfg)
        st.success(f"Ego '{selected}' loaded.")
        st.rerun()

with ego_col3:
    if st.button("Delete", width='stretch',
                 disabled=(selected == "— none / custom —")):
        egos.pop(selected, None)
        cfg["egos"] = egos
        if cfg.get("active_ego") == selected:
            cfg.pop("active_ego", None)
        _save(cfg)
        st.success(f"Ego '{selected}' deleted.")
        st.rerun()

# Save new ego
if len(ego_names) < MAX_EGOS:
    save_col1, save_col2 = st.columns([2, 1])
    with save_col1:
        new_ego_name = st.text_input("Save current personality as ego",
                                     placeholder="Ego name…",
                                     label_visibility="collapsed")
    with save_col2:
        if st.button("Save Ego", width='stretch',
                     disabled=not new_ego_name.strip()):
            name = new_ego_name.strip()
            cfg.setdefault("egos", {})[name] = _ego_snapshot(cfg)
            cfg["active_ego"] = name
            _save(cfg)
            st.success(f"Ego '{name}' saved.")
            st.rerun()
else:
    st.caption(f"Max {MAX_EGOS} egos reached. Delete one to save a new ego.")

if ego_names:
    st.caption("Saved: " + " · ".join(
        f"**{n}**" if n == cfg.get("active_ego") else n for n in ego_names
    ))

st.divider()

# ── Learning Pipeline ─────────────────────────────────────────────────────────
st.subheader("Learning Pipeline")
st.caption("cycle_time and n_workers require a Streamlit restart to apply.")

col1, col2, col3 = st.columns(3)

with col1:
    v = st.number_input("Cycle time (s)", 3, 120, int(_get("learning","cycle_time",10)), help="Seconds between cycles.")
    _set("learning","cycle_time",v)
    v = st.number_input("Parallel workers", 1, 16, int(_get("learning","n_workers",4)), help="Thread pool size.")
    _set("learning","n_workers",v)

with col2:
    v = st.number_input("Topics per cycle", 1, 30, int(_get("learning","topics_per_cycle",6)))
    _set("learning","topics_per_cycle",v)
    v = st.number_input("Min sentences", 1, 20, int(_get("learning","min_sentences",3)), help="Skip content with fewer sentences.")
    _set("learning","min_sentences",v)

with col3:
    v = st.number_input("Fetch timeout (s)", 5, 60, int(_get("learning","fetch_timeout",18)))
    _set("learning","fetch_timeout",v)
    v = st.number_input("Search timeout (s)", 3, 30, int(_get("learning","search_timeout",7)))
    _set("learning","search_timeout",v)

st.divider()

# ── Energy Budget ─────────────────────────────────────────────────────────────
st.subheader("Energy Budget")
col_e1, col_e2 = st.columns(2)

with col_e1:
    v = st.slider("Replenish per tick", 0.01, 0.30, float(_get("energy","replenish_per_tick",0.08)), 0.01)
    _set("energy","replenish_per_tick",v)
    v = st.slider("Cost: simulation/step", 0.01, 0.25, float(_get("energy","cost_simulation",0.04)), 0.01)
    _set("energy","cost_simulation",v)
    v = st.slider("Cost: exploration", 0.01, 0.25, float(_get("energy","cost_exploration",0.06)), 0.01)
    _set("energy","cost_exploration",v)

with col_e2:
    v = st.slider("Cost: region switch", 0.01, 0.25, float(_get("energy","cost_region_switch",0.05)), 0.01)
    _set("energy","cost_region_switch",v)
    v = st.slider("Cost: abstraction run", 0.01, 0.30, float(_get("energy","cost_abstraction",0.10)), 0.01)
    _set("energy","cost_abstraction",v)

st.divider()

# ── Attention and Goals ───────────────────────────────────────────────────────
st.subheader("Attention & Goal Drives")
col_a1, col_a2 = st.columns(2)

with col_a1:
    v = st.slider("Bias strength", 0.0, 1.0, float(_get("attention","bias_strength",0.0)), 0.05)
    _set("attention","bias_strength",v)
    v = st.slider("Novelty strength", 0.0, 1.0, float(_get("attention","novelty_strength",0.0)), 0.05)
    _set("attention","novelty_strength",v)

with col_a2:
    st.caption("Goal drive weights")
    drives = {"reduce_uncertainty":0.30,"increase_novelty":0.25,"resolve_contradiction":0.20,"maintain_stability":0.15,"expand_regions":0.10}
    for k, dv in drives.items():
        v = st.slider(k.replace("_"," ").title(), 0.0, 1.0, float(_get("goals",k,dv)), 0.05, key="goal_"+k)
        _set("goals",k,v)

st.divider()

# ── Personality ───────────────────────────────────────────────────────────────
st.subheader("Personality")
col_p1, col_p2 = st.columns(2)

with col_p1:
    st.markdown("**Identity drift**")
    v = st.slider("Drift rate", 0.001, 0.10, float(_get("identity","drift_rate",0.02)), 0.001, format="%.3f")
    _set("identity","drift_rate",v)

    st.markdown("**Evolver**")
    v = st.number_input("Adapt every N ticks", 5, 200, int(_get("evolver","adapt_every",30)))
    _set("evolver","adapt_every",v)
    v = st.slider("Max delta per adaptation", 0.01, 0.20, float(_get("evolver","max_delta",0.05)), 0.01)
    _set("evolver","max_delta",v)

with col_p2:
    st.markdown("**Meta-state**")
    v = st.slider("Concept decay rate", 0.90, 0.999, float(_get("meta_state","decay_rate",0.97)), 0.001, format="%.3f")
    _set("meta_state","decay_rate",v)
    v = st.slider("Reinforce gain", 0.05, 0.80, float(_get("meta_state","reinforce_gain",0.25)), 0.05)
    _set("meta_state","reinforce_gain",v)
    v = st.slider("Fatigue (repetition penalty)", 0.0, 1.0, float(_get("meta_state","fatigue_k",0.2)), 0.05)
    _set("meta_state","fatigue_k",v)
    v = st.slider("Cooling alpha", 0.0, 1.0, float(_get("meta_state","cooling_alpha",0.5)), 0.05)
    _set("meta_state","cooling_alpha",v)

st.divider()

# ── Save / Reset ──────────────────────────────────────────────────────────────
col_save, col_reset = st.columns([1, 3])

with col_save:
    if st.button("Save & Apply", type="primary", width='stretch'):
        _save(cfg)
        st.success("Saved. Changes apply within the next learner cycle.")

with col_reset:
    if st.button("Reset to defaults", width='stretch'):
        egos_backup = cfg.get("egos", {})
        active_backup = cfg.get("active_ego", "")
        defaults = {
            "learning":   {"cycle_time":10,"n_workers":4,"topics_per_cycle":6,"min_sentences":3,"fetch_timeout":18,"search_timeout":7,"checkin_every":10800,"feed_max":200},
            "energy":     {"total":1.0,"replenish_per_tick":0.08,"cost_simulation":0.04,"cost_exploration":0.06,"cost_region_switch":0.05,"cost_abstraction":0.10},
            "identity":   {"drift_rate":0.02},
            "evolver":    {"adapt_every":30,"max_delta":0.05},
            "attention":  {"bias_strength":0.0,"novelty_strength":0.0},
            "meta_state": {"decay_rate":0.97,"reinforce_gain":0.25,"fatigue_k":0.2,"cooling_alpha":0.5},
            "goals":      {"reduce_uncertainty":0.30,"increase_novelty":0.25,"resolve_contradiction":0.20,"maintain_stability":0.15,"expand_regions":0.10},
            "egos":       egos_backup,
            "active_ego": active_backup,
        }
        _save(defaults)
        st.success("Reset to defaults. Egos preserved.")
        st.rerun()

with st.expander("Raw engine_config.json"):
    try:
        st.json(json.loads(_CFG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        st.error(str(e))
