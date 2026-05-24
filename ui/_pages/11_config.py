"""Settings - live-editable engine parameters."""
import sys, os, json, subprocess, re, time
from pathlib import Path
import streamlit as st

_ROOT      = Path(__file__).parent.parent.parent
_CFG_PATH  = _ROOT / "data" / "engine_config.json"
_CF_BIN    = _ROOT / "cloudflared.exe"
_CF_LOG    = _ROOT / "data" / "tunnel.log"
_CF_PID    = _ROOT / "data" / "tunnel.pid"
_VZ_LOG    = _ROOT / "data" / "viz_tunnel.log"
_VZ_PID    = _ROOT / "data" / "viz_tunnel.pid"

sys.path.insert(0, str(_ROOT / "src"))


# ── Tunnel helpers ────────────────────────────────────────────────────────────

def _tunnel_running() -> bool:
    try:
        pid = int(_CF_PID.read_text())
    except Exception:
        return False
    # psutil first
    try:
        import psutil
        if psutil.pid_exists(pid):
            return True
    except Exception:
        pass
    # tasklist fallback (more reliable on Windows for detached processes)
    try:
        r = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
            capture_output=True, text=True, timeout=3,
        )
        return 'cloudflared' in r.stdout.lower()
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
    # kill any stale process first
    _stop_tunnel()
    _CF_LOG.write_text('', encoding='utf-8')
    flags = 0
    if os.name == 'nt':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    log_fh = open(_CF_LOG, 'w', encoding='utf-8')
    proc = subprocess.Popen(
        [str(_CF_BIN), 'tunnel', '--url', 'http://localhost:8501'],
        stdout=log_fh, stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
    )
    log_fh.close()
    _CF_PID.write_text(str(proc.pid))
    return ''

def _stop_tunnel():
    try:
        pid = int(_CF_PID.read_text())
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                           capture_output=True, text=True, timeout=3)
        if 'cloudflared' in r.stdout.lower():
            subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                           capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        import psutil
        pid = int(_CF_PID.read_text())
        psutil.Process(pid).terminate()
    except Exception:
        pass
    try: _CF_PID.unlink()
    except Exception: pass


# ── Visualizer tunnel helpers (port 8502) ────────────────────────────────────

def _vz_tunnel_running() -> bool:
    try:
        pid = int(_VZ_PID.read_text())
    except Exception:
        return False
    try:
        import psutil
        if psutil.pid_exists(pid):
            return True
    except Exception:
        pass
    try:
        r = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
            capture_output=True, text=True, timeout=3,
        )
        return 'cloudflared' in r.stdout.lower()
    except Exception:
        return False

def _vz_tunnel_url() -> str:
    try:
        log = _VZ_LOG.read_text(encoding='utf-8', errors='ignore')
        m   = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', log)
        return m.group(0) if m else ''
    except Exception:
        return ''

def _start_vz_tunnel():
    if not _CF_BIN.exists():
        return 'cloudflared.exe not found'
    _stop_vz_tunnel()
    _VZ_LOG.write_text('', encoding='utf-8')
    flags = 0
    if os.name == 'nt':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    log_fh = open(_VZ_LOG, 'w', encoding='utf-8')
    proc = subprocess.Popen(
        [str(_CF_BIN), 'tunnel', '--url', 'http://localhost:8502'],
        stdout=log_fh, stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
    )
    log_fh.close()
    _VZ_PID.write_text(str(proc.pid))
    return ''

def _stop_vz_tunnel():
    try:
        pid = int(_VZ_PID.read_text())
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                           capture_output=True, text=True, timeout=3)
        if 'cloudflared' in r.stdout.lower():
            subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                           capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        import psutil
        pid = int(_VZ_PID.read_text())
        psutil.Process(pid).terminate()
    except Exception:
        pass
    try: _VZ_PID.unlink()
    except Exception: pass

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
    padding-bottom: 6px !important; margin-top: 28px !important;
}
p, .stMarkdown p { color: #9898c8 !important; font-size: 14px !important; }
strong { color: #b0b0d8 !important; }
caption, .stCaption { color: #505078 !important; font-size: 12px !important; letter-spacing: 0.06em !important; }
hr { border-color: #222238 !important; margin: 20px 0 !important; }
[data-testid="stExpander"] { border: 1px solid #1a1a28 !important; background: #0a0a10 !important; }
[data-testid="stExpanderDetails"] { background: #07070a !important; }
summary[data-testid="stExpanderToggle"] span { color: #6868a0 !important; font-size: 13px !important; }
[data-testid="stAlert"] { background: #0a0a12 !important; border-color: #222238 !important; }
textarea, input[type="text"], input[type="number"] {
    background: #0a0a12 !important; border: 1px solid #222238 !important;
    color: #b0b0d8 !important; font-size: 13px !important;
}
[data-baseweb="select"] > div { background: #0a0a12 !important; border-color: #222238 !important; color: #9898c8 !important; }
[data-testid="stSlider"] > div > div { background: #222238 !important; }
label { color: #6868a0 !important; font-size: 12px !important; letter-spacing: 0.08em !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div style="padding:16px 0 4px;border-bottom:1px solid #222238;margin-bottom:20px">
  <div style="font-size:22px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;font-weight:400">SETTINGS</div>
  <div style="font-size:12px;color:#505078;letter-spacing:0.06em;margin-top:4px">changes apply within one learner cycle (~10 s) · no restart needed unless noted</div>
</div>
""", unsafe_allow_html=True)

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
                st.info("Starting… URL appears in ~8 seconds.")
                time.sleep(8)
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

# ── Visualizer Public Access (port 8502) ──────────────────────────────────────
st.markdown("""
<div style="font-size:12px;letter-spacing:0.2em;text-transform:uppercase;color:#505078;
            border-bottom:1px solid #222238;padding-bottom:6px;margin:18px 0 14px">
  Visualizer Public Access
</div>
""", unsafe_allow_html=True)

vz_running = _vz_tunnel_running()
vz_col1, vz_col2 = st.columns([1, 3])

with vz_col1:
    if vz_running:
        if st.button("Stop Viz Tunnel", width='stretch'):
            _stop_vz_tunnel()
            st.success("Visualizer tunnel stopped.")
            st.rerun()
    else:
        if st.button("Start Viz Tunnel", type="primary", width='stretch'):
            err = _start_vz_tunnel()
            if err:
                st.error(err)
            else:
                st.info("Starting… URL appears in ~8 seconds.")
                time.sleep(8)
                st.rerun()

with vz_col2:
    if vz_running:
        vz_url = _vz_tunnel_url()
        if vz_url:
            st.success(f"Live: [{vz_url}]({vz_url})")
            st.caption("Share this link — anyone can view the graph visualizer (port 8502) from outside your network.")
        else:
            st.info("Tunnel starting — URL loading…")
            time.sleep(3)
            st.rerun()
    else:
        if not _CF_BIN.exists():
            st.caption("cloudflared.exe not found in project root. Download it to enable public access.")
        else:
            st.caption("Creates a free Cloudflare public URL for the visualizer on localhost:8502.")

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

st.caption("All engine parameters (learning speed, goals, energy, personality) are in the YTP page.")

with st.expander("Raw engine_config.json"):
    try:
        st.json(json.loads(_CFG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        st.error(str(e))
