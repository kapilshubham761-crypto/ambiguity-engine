"""Cognitive Core Panel — machine state monitor."""
import sys, os, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _HAS_NVML = True
except Exception:
    _HAS_NVML = False
    _GPU_HANDLE = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / 'data'
DB   = DATA / 'graph.db'


# ── Data helpers ──────────────────────────────────────────────────────────────

def _j(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:
        return default or {}

def _txt(p, default=''):
    try:
        return Path(p).read_text(encoding='utf-8').strip()
    except Exception:
        return default

def _db(sql, default=0):
    try:
        with sqlite3.connect(DB) as c:
            return c.execute(sql).fetchone()[0]
    except Exception:
        return default


# ── Read state ────────────────────────────────────────────────────────────────

cog     = _j(DATA / 'cog_status.json',   {})
energy  = _j(DATA / 'energy.json',        {})
ident   = _j(DATA / 'identity.json',      {})
ms_data = _j(DATA / 'meta_state.json',    {})
stats   = _j(DATA / 'learner_stats.json', {})
fetch   = _j(DATA / 'fetch_status.json',  {})
paused  = _txt(DATA / 'paused.txt') == '1'
contra_raw = _j(DATA / 'contradictions.json', [])

mode = cog.get('mode', 'idle')
goal = cog.get('goal', 'unknown').replace('_', ' ')

STATUS_WORD, STATUS_COL = (
    ('PAUSED',   '#ff4444') if paused else
    ('FETCHING', '#4a9eff') if fetch.get('fetching') else
    ('RUNNING',  '#44ff88')
)

MODE_COL = {
    'focused':      '#f59e0b',
    'exploratory':  '#3b82f6',
    'associative':  '#8b5cf6',
    'exploitative': '#ef4444',
    'reflective':   '#10b981',
}
mode_col = MODE_COL.get(mode, '#555')

traits      = ident.get('traits', {})
energy_val  = float(energy.get('current', 1.0))
exploration = float(traits.get('exploration_style',    0.3))
stability   = float(traits.get('stability_bias',       0.5))
novelty     = float(traits.get('novelty_bias',         0.5))
abst_depth  = float(traits.get('abstraction_depth',    0.5))
contra_tol  = float(traits.get('contradiction_tolerance', 0.5))

# Meta-state top concepts
ms_entries = ms_data.get('entries', {})
top_concepts = []
for k, v in ms_entries.items():
    val = v.get('value', 0) if isinstance(v, dict) else v
    try:
        top_concepts.append((str(k), float(val)))
    except Exception:
        pass
top_concepts.sort(key=lambda x: -x[1])
top5 = top_concepts[:5]

_coh_raw   = sum(v for _, v in top5) / max(len(top5), 1) if top5 else 0.0
coherence  = round(_coh_raw, 4)   # keep full precision; display rounds below
ambiguity  = round(exploration * 0.6 + (1 - stability) * 0.4, 2)
volatility = round(exploration, 2)
pressure   = 'HIGH' if energy_val < 0.3 else 'MED' if energy_val < 0.6 else 'LOW'

# Graph
n_nodes     = _db("SELECT COUNT(*) FROM nodes")
n_edges     = _db("SELECT COUNT(*) FROM edges")
n_sentences = stats.get('total_sentences', 0)

# Growth
growth_str = '—'
try:
    gl = (DATA / 'growth_log.jsonl').read_text(encoding='utf-8').splitlines()
    if len(gl) >= 2:
        delta = json.loads(gl[-1])['nodes'] - json.loads(gl[0])['nodes']
        growth_str = f'+{delta:,}'
except Exception:
    pass

# System stats
cpu_s = ram_s = gpu_s = temp_s = '—'
if _HAS_PSUTIL:
    _cpu = psutil.cpu_percent(interval=None)
    _ram = psutil.virtual_memory()
    cpu_s = f'{_cpu:.0f}%'
    ram_s = f'{_ram.used/1e9:.1f}G'
if _HAS_NVML and _GPU_HANDLE:
    try:
        _u = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
        _t = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        gpu_s  = f'{_u.gpu}%'
        temp_s = f'{_t}°C'
    except Exception:
        pass

# Elapsed fetch
elapsed_s = ''
if fetch.get('fetching') and fetch.get('started_at'):
    try:
        st_ = datetime.fromisoformat(fetch['started_at'])
        if st_.tzinfo is None:
            st_ = st_.replace(tzinfo=timezone.utc)
        sec = int((datetime.now(tz=timezone.utc) - st_).total_seconds())
        m, s = divmod(sec, 60)
        elapsed_s = f'{m}m{s:02d}s' if m else f'{s}s'
    except Exception:
        pass

# Contradictions
unresolved_c = len([c for c in (contra_raw if isinstance(contra_raw, list) else [])
                    if c.get('resolution_status', '') == 'open'])

# Feed
feed = []
fp = DATA / 'live_feed.jsonl'
if fp.exists():
    try:
        for ln in reversed(fp.read_text(encoding='utf-8').splitlines()[-100:]):
            try:
                feed.append(json.loads(ln))
            except Exception:
                pass
    except Exception:
        pass

last_topic  = feed[0].get('topic',  '—') if feed else '—'
last_source = feed[0].get('source', '—') if feed else '—'
now_s  = datetime.now().strftime('%H:%M:%S')
date_s = datetime.now().strftime('%Y-%m-%d')

# AIQ — learning rate from recent feed entries
aiq_cpm    = 0.0   # concepts per minute
aiq_aph    = 0.0   # articles per hour
aiq_str    = '—'
aiq_aph_str = '—'
try:
    ok_feed = [e for e in feed if e.get('status') == 'ok'
               and e.get('ts') and e.get('concepts', 0) > 0][:30]
    if len(ok_feed) >= 2:
        def _parse_ts(s):
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        newest  = _parse_ts(ok_feed[0]['ts'])
        oldest  = _parse_ts(ok_feed[-1]['ts'])
        span_s  = max(1.0, (newest - oldest).total_seconds())
        total_c = sum(e.get('concepts', 0) for e in ok_feed)
        aiq_cpm = total_c / (span_s / 60)
        aiq_aph = len(ok_feed) / (span_s / 3600)
        aiq_str     = f'{aiq_cpm:.1f}/min'
        aiq_aph_str = f'{aiq_aph:.0f}/hr'
except Exception:
    pass

# AIQ colour — dim when low, bright when active
aiq_col = '#44ff88' if aiq_cpm > 20 else '#f59e0b' if aiq_cpm > 5 else '#505078'


# ── Boot learner ──────────────────────────────────────────────────────────────

from learner import AutoLearner

@st.cache_resource(show_spinner=False)
def _boot():
    l = AutoLearner.get()
    l.start()
    return l

_boot()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bar(val: float, width: int = 10) -> str:
    filled = max(0, min(width, round(float(val) * width)))
    return (
        f'<span style="color:#3a5a5a;letter-spacing:-1px">{"▮" * filled}</span>'
        f'<span style="color:#1a1a28;letter-spacing:-1px">{"▮" * (width - filled)}</span>'
    )

def _ebar(val: float, width: int = 10) -> str:
    filled = max(0, min(width, round(float(val) * width)))
    col = '#ff4444' if val < 0.3 else '#ffaa44' if val < 0.6 else '#44ff88'
    return (
        f'<span style="color:{col};letter-spacing:-1px">{"▮" * filled}</span>'
        f'<span style="color:#141418;letter-spacing:-1px">{"▮" * (width - filled)}</span>'
    )

def _ev_age(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime('%m-%d  %H:%M:%S')
    except Exception:
        return '——-——  ——:——:——'

def _ev_dim(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(tz=timezone.utc) - dt).total_seconds()
        return max(0.0, 1.0 - age / 7200)
    except Exception:
        return 0.1

def _esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ══════════════════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(f"""
<style>
#MainMenu, footer, header[data-testid="stHeader"] {{ display: none !important; }}

body, .stApp {{
    background: #070709 !important;
}}

.block-container {{
    padding: 0 !important;
    max-width: 100% !important;
}}

*, *::before, *::after {{
    font-family: 'Consolas', 'Courier New', monospace !important;
}}

/* Restore Streamlit's icon glyph font — prevents "arrow_right" literal text */
[data-testid="stExpanderToggleIcon"],
[data-testid="stExpanderToggleIcon"] *,
[data-testid="stExpanderToggleIcon"] span,
[data-baseweb="icon"] span,
.material-icons, .material-symbols-rounded, .material-symbols-outlined,
span[class*="material-symbols"], span[class*="material-icons"],
[class*="MaterialIcon"], [class*="materialIcon"] {{
    font-family: 'Material Symbols Rounded','Material Symbols Outlined','Material Icons',sans-serif !important;
    font-weight: normal !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-smoothing: antialiased !important;
}}

/* ── STATUS BAR ── */
.ae-sb {{
    position: sticky;
    top: 0;
    z-index: 8000;
    background: #0a0a12;
    border-bottom: 1px solid #222238;
    padding: 8px 28px;
    display: flex;
    gap: 0;
    align-items: center;
    font-size: 15px;
    letter-spacing: 0.05em;
}}
.ae-sb-status {{ color: {STATUS_COL}; font-weight: bold; letter-spacing: 0.14em; }}
.ae-sb-mode   {{ color: {mode_col}; }}
.ae-sb-val    {{ color: #b0b0d8; }}
.ae-sb-dim    {{ color: #7878a8; }}
.ae-sb-sep    {{ color: #4a4a70; margin: 0 16px; }}
.ae-sb-right  {{ margin-left: auto; color: #606090; font-size: 13px; letter-spacing: 0.04em; }}
.ae-cursor    {{ animation: ae-blink 1.1s step-end infinite; }}
@keyframes ae-blink {{ 0%,100%{{ opacity:1; }} 50%{{ opacity:0; }} }}

/* ── CENTRAL STATE ── */
.ae-central {{
    text-align: center;
    padding: 48px 20px 36px;
    border-bottom: 1px solid #222238;
    background: #070709;
}}
.ae-mode-glyph {{
    font-size: 84px;
    letter-spacing: 12px;
    text-transform: uppercase;
    color: {mode_col};
    line-height: 1;
    text-shadow: 0 0 100px {mode_col}44;
}}
.ae-goal-text {{
    font-size: 18px;
    letter-spacing: 6px;
    text-transform: uppercase;
    color: #7878a0;
    margin: 14px 0 34px;
}}
.ae-diag {{
    display: inline-grid;
    grid-template-columns: repeat(5, 130px);
    gap: 0 20px;
    text-align: center;
}}
.ae-diag-cell {{ }}
.ae-diag-label {{
    font-size: 12px;
    letter-spacing: 0.16em;
    color: #505078;
    text-transform: uppercase;
    display: block;
    margin-bottom: 4px;
}}
.ae-diag-val {{
    font-size: 42px;
    color: #9090c0;
    line-height: 1;
}}

/* ── SUBSYSTEM PANELS ── */
.ae-panels {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    border-bottom: 1px solid #222238;
    background: #060608;
}}
.ae-panel {{
    padding: 20px 24px;
    border-right: 1px solid #222238;
    overflow: hidden;
}}
.ae-panel:last-child {{ border-right: none; }}
.ae-ptitle {{
    font-size: 12px;
    letter-spacing: 0.2em;
    color: #505078;
    text-transform: uppercase;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #222238;
}}
.ae-prow {{
    display: flex;
    justify-content: space-between;
    font-size: 15px;
    padding: 3px 0;
    letter-spacing: 0.03em;
    gap: 8px;
}}
.ae-pkey {{
    min-width: 100px;
    flex-shrink: 0;
    color: #6868a0;
}}
.ae-pval {{
    color: #9898c8;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: right;
}}
.ae-pval-accent {{ color: {mode_col}; }}
.ae-pval-warn   {{ color: #ff6644; }}
.ae-pval-ok     {{ color: #44ff88; opacity: 0.8; }}
.ae-concept {{
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 13px;
    padding: 2px 0;
    color: #6868a0;
}}
.ae-cn {{ min-width: 100px; overflow: hidden; white-space: nowrap; color: #7878a8; }}

/* ── TELEMETRY ── */
.ae-tel {{
    padding: 10px 28px;
    background: #0a0a12;
    border-bottom: 1px solid #222238;
    font-size: 13px;
    display: flex;
    gap: 0;
    align-items: center;
    flex-wrap: wrap;
    letter-spacing: 0.05em;
}}
.ae-tel-sep   {{ color: #383860; margin: 0 18px; }}
.ae-tel-label {{ color: #505078; text-transform: uppercase; font-size: 12px; margin-right: 6px; }}
.ae-tel-val   {{ color: #8080a8; }}
.ae-tel-val-warn {{ color: #ff6644; }}
.ae-tel-val-ok   {{ color: #44ff88; opacity: 0.75; }}

/* ── EVENT STREAM ── */
.ae-stream {{
    padding: 16px 28px 28px;
    background: #0a0a12;
}}
.ae-stream-title {{
    font-size: 12px;
    letter-spacing: 0.2em;
    color: #606090;
    text-transform: uppercase;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid #222238;
}}
.ae-ev {{
    font-size: 13px;
    padding: 2px 0;
    letter-spacing: 0.02em;
    line-height: 1.7;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.ae-ev-ts    {{ color: #606090; }}
.ae-ev-nc    {{ color: {mode_col}; opacity: 0.9; min-width: 50px; display:inline-block; }}
.ae-ev-ttl   {{ color: #9090c0; }}
.ae-ev-src   {{ color: #686898; }}

/* ── EXPANDERS ── */
[data-testid="stExpander"] {{
    border: 1px solid #1a1a28 !important;
    background: #0a0a10 !important;
}}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# STATUS BAR
# ══════════════════════════════════════════════════════════════════════════════

status_extra = f'&nbsp;<span class="ae-sb-dim">{elapsed_s}</span>' if elapsed_s else ''

st.markdown(f"""
<div class="ae-sb">
  <span class="ae-sb-status">{STATUS_WORD}<span class="ae-cursor">_</span></span>{status_extra}
  <span class="ae-sb-sep">│</span>
  <span class="ae-sb-dim">mode</span>&nbsp;<span class="ae-sb-mode">{_esc(mode)}</span>
  <span class="ae-sb-sep">│</span>
  <span class="ae-sb-dim">goal</span>&nbsp;<span class="ae-sb-val">{_esc(goal)}</span>
  <span class="ae-sb-sep">│</span>
  <span class="ae-sb-dim">energy</span>&nbsp;<span class="ae-sb-val">{energy_val:.2f}</span>
  <span class="ae-sb-sep">│</span>
  <span class="ae-sb-dim">coherence</span>&nbsp;<span class="ae-sb-val">{coherence:.4f}</span>
  <span class="ae-sb-sep">│</span>
  <span class="ae-sb-dim">nodes</span>&nbsp;<span class="ae-sb-val">{n_nodes:,}</span>
  <span class="ae-sb-sep">│</span>
  <span class="ae-sb-dim">aiq</span>&nbsp;<span style="color:{aiq_col}">{aiq_str}</span>
  <span class="ae-sb-right">{date_s}&nbsp;&nbsp;{now_s}</span>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# CENTRAL STATE ENGINE
# ══════════════════════════════════════════════════════════════════════════════

pressure_col = '#ff4444' if pressure == 'HIGH' else '#ffaa44' if pressure == 'MED' else '#3a3a4a'

st.markdown(f"""
<div class="ae-central">
  <div class="ae-mode-glyph">{_esc(mode)}</div>
  <div class="ae-goal-text">{_esc(goal)}</div>
  <div class="ae-diag">
    <div class="ae-diag-cell">
      <span class="ae-diag-label">coherence</span>
      <span class="ae-diag-val">{coherence:.4f}</span>
    </div>
    <div class="ae-diag-cell">
      <span class="ae-diag-label">ambiguity</span>
      <span class="ae-diag-val">{ambiguity:.2f}</span>
    </div>
    <div class="ae-diag-cell">
      <span class="ae-diag-label">volatility</span>
      <span class="ae-diag-val">{volatility:.2f}</span>
    </div>
    <div class="ae-diag-cell">
      <span class="ae-diag-label">pressure</span>
      <span class="ae-diag-val" style="color:{pressure_col};font-size:22px">{pressure}</span>
    </div>
    <div class="ae-diag-cell">
      <span class="ae-diag-label">aiq</span>
      <span class="ae-diag-val" style="color:{aiq_col};font-size:28px">{aiq_str}</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM PANELS — INPUT | MEMORY | OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

# Build concept rows for INPUT
concept_rows = ''
for name, val in top_concepts[:8]:
    short = (name[:14] + '…') if len(name) > 14 else name
    concept_rows += (
        f'<div class="ae-concept">'
        f'<span class="ae-cn">{_esc(short)}</span>'
        f'{_bar(min(1.0, val))}'
        f'<span style="color:#4a4a6a;font-size:13px">&nbsp;{val:.2f}</span>'
        f'</div>'
    )
if not concept_rows:
    concept_rows = '<div class="ae-prow"><span class="ae-pkey">signals</span><span class="ae-pval">awaiting data</span></div>'

# Contradictions
contra_col = 'ae-pval-warn' if unresolved_c > 0 else 'ae-pval'

# Energy bar for OUTPUT
ebar_html = _ebar(energy_val)
spend_count  = energy.get('spend_count', 0)
denied_count = energy.get('denied_count', 0)

# Identity rows
id_rows = ''
for k, v in traits.items():
    short_k = k.replace('_', ' ')[:18]
    id_rows += (
        f'<div class="ae-concept">'
        f'<span class="ae-cn" style="min-width:100px">{_esc(short_k)}</span>'
        f'{_bar(min(1.0, float(v)))}'
        f'<span style="color:#4a4a6a;font-size:13px">&nbsp;{float(v):.2f}</span>'
        f'</div>'
    )

# Modulation state
mod_level = 'high' if ambiguity > 0.6 else 'medium' if ambiguity > 0.3 else 'low'
mod_col   = '#ef4444' if mod_level == 'high' else '#f59e0b' if mod_level == 'medium' else '#555'

st.markdown(f"""
<div class="ae-panels">

  <!-- INPUT -->
  <div class="ae-panel">
    <div class="ae-ptitle">▸ input layer</div>
    <div class="ae-prow">
      <span class="ae-pkey">active signals</span>
      <span class="ae-pval">{len(top_concepts)}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">unresolved</span>
      <span class="{contra_col}">{unresolved_c} contradictions</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">last trigger</span>
      <span class="ae-pval" style="font-size:9px">{_esc(last_topic[:24])}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">source</span>
      <span class="ae-pval">{_esc(last_source)}</span>
    </div>
    <div style="margin-top:8px;border-top:1px solid #1a1a28;padding-top:6px">
      {concept_rows}
    </div>
  </div>

  <!-- MEMORY -->
  <div class="ae-panel">
    <div class="ae-ptitle">▸ memory layer</div>
    <div class="ae-prow">
      <span class="ae-pkey">nodes</span>
      <span class="ae-pval-accent">{n_nodes:,}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">edges</span>
      <span class="ae-pval">{n_edges:,}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">sentences</span>
      <span class="ae-pval">{n_sentences:,}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">growth</span>
      <span class="ae-pval-ok">{growth_str}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">learn rate</span>
      <span class="ae-pval" style="color:{aiq_col}">{aiq_str} concepts</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">articles</span>
      <span class="ae-pval">{aiq_aph_str} ingested</span>
    </div>
    <div style="margin-top:8px;border-top:1px solid #1a1a28;padding-top:6px">
      <div class="ae-ptitle" style="margin-bottom:6px">identity traits</div>
      {id_rows}
    </div>
  </div>

  <!-- OUTPUT -->
  <div class="ae-panel">
    <div class="ae-ptitle">▸ output layer</div>
    <div class="ae-prow">
      <span class="ae-pkey">intent</span>
      <span class="ae-pval-accent">{_esc(goal)}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">action</span>
      <span class="ae-pval">{'paused' if paused else 'fetching' if fetch.get('fetching') else 'learning'}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">modulation</span>
      <span style="color:{mod_col};font-size:10px">{mod_level}</span>
    </div>
    <div class="ae-prow">
      <span class="ae-pkey">mode</span>
      <span style="color:{mode_col};font-size:10px">{_esc(mode)}</span>
    </div>
    <div style="margin-top:8px;border-top:1px solid #1a1a28;padding-top:6px">
      <div class="ae-ptitle" style="margin-bottom:6px">energy budget</div>
      <div class="ae-prow">
        <span class="ae-pkey">level</span>
        <span>{ebar_html}&nbsp;<span style="color:#353548">{energy_val:.2f}</span></span>
      </div>
      <div class="ae-prow">
        <span class="ae-pkey">spent</span>
        <span class="ae-pval">{spend_count} ops</span>
      </div>
      <div class="ae-prow">
        <span class="ae-pkey">denied</span>
        <span class="{'ae-pval-warn' if denied_count > 0 else 'ae-pval'}">{denied_count}</span>
      </div>
      <div class="ae-prow" style="margin-top:6px">
        <span class="ae-pkey">stability</span>
        <span>{_bar(stability)}&nbsp;<span style="color:#252535">{stability:.2f}</span></span>
      </div>
      <div class="ae-prow">
        <span class="ae-pkey">novelty</span>
        <span>{_bar(novelty)}&nbsp;<span style="color:#252535">{novelty:.2f}</span></span>
      </div>
    </div>
  </div>

</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM TELEMETRY
# ══════════════════════════════════════════════════════════════════════════════

cpu_val  = float(cpu_s.replace('%', '')) if cpu_s != '—' else 0
ram_col  = 'ae-tel-val-warn' if cpu_val > 80 else 'ae-tel-val-ok' if cpu_val < 40 else 'ae-tel-val'
gpu_val  = float(gpu_s.replace('%', '')) if gpu_s != '—' else 0
gpu_col  = 'ae-tel-val-warn' if gpu_val > 80 else 'ae-tel-val'

st.markdown(f"""
<div class="ae-tel">
  <span class="ae-tel-label">CPU</span><span class="ae-tel-val">{cpu_s}</span>
  <span class="ae-tel-sep">·</span>
  <span class="ae-tel-label">RAM</span><span class="ae-tel-val">{ram_s}</span>
  <span class="ae-tel-sep">·</span>
  <span class="ae-tel-label">GPU</span><span class="{gpu_col}">{gpu_s}</span>
  <span class="ae-tel-sep">·</span>
  <span class="ae-tel-label">TEMP</span><span class="ae-tel-val">{temp_s}</span>
  <span class="ae-tel-sep" style="margin:0 24px">║</span>
  <span class="ae-tel-label">nodes</span><span class="ae-tel-val">{n_nodes:,}</span>
  <span class="ae-tel-sep">·</span>
  <span class="ae-tel-label">edges</span><span class="ae-tel-val">{n_edges:,}</span>
  <span class="ae-tel-sep">·</span>
  <span class="ae-tel-label">sentences</span><span class="ae-tel-val">{n_sentences:,}</span>
  <span class="ae-tel-sep">·</span>
  <span class="ae-tel-label">growth</span><span class="ae-tel-val-ok">{growth_str}</span>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE EFFICIENCY — block diagram
# ══════════════════════════════════════════════════════════════════════════════

perf = _j(DATA / 'perf_profile.json', {})

# Cache valid data in session_state — survive restarts before first cycle completes
_perf_stale = False
if perf.get('process_avg_s'):
    st.session_state['_perf_cache'] = perf
elif '_perf_cache' in st.session_state:
    perf = st.session_state['_perf_cache']
    _perf_stale = True

_pa  = perf.get('process_avg_s', {})
_pp  = perf.get('process_pct',   {})
_cyc = perf.get('cycle',         {})
_ns  = perf.get('n_samples',     0)

def _blk(label, secs, pct, flag=''):
    """Render one pipeline block with bar fill."""
    bar_w  = max(1, int(pct * 0.6))   # max 60 chars wide
    col    = '#ef4444' if pct > 40 else '#f59e0b' if pct > 20 else '#4a9eff'
    if flag == 'bottleneck':
        col = '#ef4444'
    bar    = '█' * bar_w + '░' * (60 - bar_w)
    return (
        f'<div style="margin:4px 0;font-size:13px">'
        f'<span style="color:#505078;min-width:80px;display:inline-block;'
        f'text-transform:uppercase;letter-spacing:0.1em;font-size:11px">{label}</span>'
        f'<span style="color:{col};letter-spacing:-1px;font-size:10px">{bar[:bar_w]}'
        f'<span style="color:#1a1a28">{bar[bar_w:]}</span></span>'
        f'&nbsp;<span style="color:{col}">{secs:.2f}s</span>'
        f'&nbsp;<span style="color:#383858">({pct:.0f}%)</span>'
        f'{"&nbsp;<span style=\\'color:#ef4444;font-size:11px\\'>◀ bottleneck</span>" if flag=="bottleneck" else ""}'
        f'</div>'
    )

if _pa:
    _steps   = ['fetch', 'extract', 'detect', 'graph', 'save', 'subsys']
    _labels  = ['fetch', 'extract (nlp)', 'detect', 'graph update', 'graph save', 'subsystems']
    _max_pct = max((_pp.get(k, 0) for k in _steps), default=1)
    _bottleneck = max(_steps, key=lambda k: _pp.get(k, 0))

    blocks_html = ''.join(
        _blk(_labels[i], _pa.get(s, 0), _pp.get(s, 0),
             flag='bottleneck' if s == _bottleneck else '')
        for i, s in enumerate(_steps)
    )

    _ok_rate   = f"{_cyc.get('items_ok', 0)}/{_cyc.get('items_found', 0)}"
    _workers   = _cyc.get('workers', '—')
    _t_search  = _cyc.get('t_search_s', 0)
    _t_process = _cyc.get('t_process_s', 0)
    _t_ticks   = _cyc.get('t_ticks_s', 0)
    _t_total   = _cyc.get('t_total_s', 0)
    _efficiency = round(_cyc.get('items_ok', 0) / max(_cyc.get('items_found', 1), 1) * 100)

    _stale_badge = (
        '&nbsp;<span style="color:#f59e0b;font-size:10px;letter-spacing:0.08em">cached · refreshing</span>'
        if _perf_stale else ''
    )

    perf_html = f"""
<div style="padding:16px 28px;background:#07070b;border-bottom:1px solid #181828">
  <div style="font-size:12px;letter-spacing:0.2em;color:#505078;text-transform:uppercase;
              border-bottom:1px solid #1a1a28;padding-bottom:6px;margin-bottom:14px">
    pipeline efficiency  ·  <span style="color:#6868a0">{_ns} samples</span>{_stale_badge}
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 40px">
    <div>
      <div style="font-size:11px;color:#404060;letter-spacing:0.12em;
                  text-transform:uppercase;margin-bottom:8px">per-article  (avg across {_ns} articles)</div>
      {blocks_html}
      <div style="margin-top:8px;font-size:12px;color:#303050;border-top:1px solid #111120;padding-top:6px">
        total per article: <span style="color:#7878a8">{_pa.get('total', 0):.2f}s</span>
      </div>
    </div>

    <div>
      <div style="font-size:11px;color:#404060;letter-spacing:0.12em;
                  text-transform:uppercase;margin-bottom:8px">last cycle  (wall clock)</div>
      <div style="font-size:13px;line-height:2">
        <div style="display:flex;justify-content:space-between">
          <span style="color:#505078">search phase</span>
          <span style="color:#9898c8">{_t_search:.1f}s</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span style="color:#505078">process phase ({_workers} workers)</span>
          <span style="color:#9898c8">{_t_process:.1f}s</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span style="color:#505078">cognitive ticks</span>
          <span style="color:#9898c8">{_t_ticks:.1f}s</span>
        </div>
        <div style="display:flex;justify-content:space-between;border-top:1px solid #1a1a28;
                    margin-top:4px;padding-top:4px">
          <span style="color:#505078">total cycle</span>
          <span style="color:#b0b0d8">{_t_total:.1f}s</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:8px">
          <span style="color:#505078">articles ok/found</span>
          <span style="color:#44ff88">{_ok_rate}</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span style="color:#505078">yield efficiency</span>
          <span style="color:{'#44ff88' if _efficiency > 60 else '#f59e0b'}">{_efficiency}%</span>
        </div>
      </div>
    </div>
  </div>
</div>
"""
else:
    perf_html = (
        '<div style="padding:12px 28px;background:#07070b;border-bottom:1px solid #181828;'
        'display:flex;align-items:center;gap:10px">'
        '<span style="font-size:12px;color:#303050">pipeline profiler</span>'
        '<span style="font-size:11px;color:#252540;letter-spacing:0.08em">'
        'waiting for first cycle · auto-refreshing</span>'
        '<span style="color:#252540;animation:ae-pulse 1.6s ease-in-out infinite">◈</span>'
        '</div>'
    )

st.markdown(perf_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# EVENT / LOG STREAM
# ══════════════════════════════════════════════════════════════════════════════

ev_rows = ''
for entry in feed[:50]:
    ts_str  = entry.get('ts', '')
    ts_disp = _ev_age(ts_str)
    dim     = _ev_dim(ts_str)
    nc      = entry.get('concepts', 0)
    title   = _esc(entry.get('title', entry.get('topic', ''))[:55])
    source  = _esc(entry.get('source', ''))
    topic   = _esc(entry.get('topic', ''))
    nc_str  = f'+{nc}' if nc else '—'
    opacity = max(0.45, dim)
    ev_rows += (
        f'<div class="ae-ev" style="opacity:{opacity:.2f}">'
        f'<span class="ae-ev-ts">[{ts_disp}]</span>'
        f'&nbsp;&nbsp;<span class="ae-ev-nc">{nc_str:>5}</span>'
        f'&nbsp;&nbsp;<span class="ae-ev-ttl">{title}</span>'
        f'&nbsp;&nbsp;<span class="ae-ev-src">[{source}/{topic}]</span>'
        f'</div>'
    )

if not ev_rows:
    ev_rows = '<div class="ae-ev" style="color:#1a1a24">awaiting first cycle…</div>'

st.markdown(f"""
<div class="ae-stream">
  <div class="ae-stream-title">event stream — cognitive activity log</div>
  {ev_rows}
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# MAINTENANCE (collapsed, minimal)
# ══════════════════════════════════════════════════════════════════════════════

import numpy as np
import pandas as pd

with st.expander("maintenance / graph tools", expanded=False):
    from graph import SemanticGraph
    from maintenance import run_maintenance, preview_maintenance, DECAY_FACTOR, PRUNE_WEIGHT, ORPHAN_DAYS

    mc1, mc2, mc3 = st.columns(3)
    decay_factor = mc1.slider("Decay", 0.80, 1.00, DECAY_FACTOR, 0.01)
    prune_weight = mc2.slider("Prune weight", 0.00, 0.50, PRUNE_WEIGHT, 0.01)
    orphan_days  = mc3.slider("Orphan days", 1, 60, ORPHAN_DAYS, 1)
    if st.button("Run maintenance", type="primary"):
        _mg = SemanticGraph()
        result = run_maintenance(_mg, force=True,
                                 decay_factor=decay_factor,
                                 prune_weight=prune_weight,
                                 orphan_days=orphan_days)
        _mg.save()
        st.cache_resource.clear()
        st.success(f"Decayed {result['edges_decayed']} edges · pruned {result['edges_pruned']} edges · removed {result['nodes_pruned']} nodes")
        st.rerun()

with st.expander("learnings / concept browser", expanded=False):
    import networkx as nx
    from graph import SemanticGraph

    @st.cache_resource(show_spinner=False)
    def _load_graph():
        return SemanticGraph()

    _lg = _load_graph()
    if _lg.node_count == 0:
        st.caption("Graph empty.")
    else:
        _tab_c, _tab_e, _tab_cl, _tab_am = st.tabs(
            ["Concepts", "Connections", "Clusters", "Ambiguity"])

        with _tab_c:
            _nodes = sorted(_lg.all_nodes(), key=lambda n: n['activation_count'], reverse=True)
            _srch  = st.text_input("Search", placeholder="concept…", label_visibility='collapsed')
            if _srch.strip():
                _nodes = [n for n in _nodes if _srch.lower() in n['text'].lower()]
            _rows = [{'concept': n['text'], 'activations': n['activation_count'],
                      'connections': _lg._g.degree(n['node_id']),
                      'first': n['first_seen'][:10], 'last': n['last_seen'][:10]}
                     for n in _nodes[:100]]
            if _rows:
                st.dataframe(pd.DataFrame(_rows), width='stretch', hide_index=True,
                             column_config={'activations': st.column_config.ProgressColumn(
                                 'activations', max_value=max(r['activations'] for r in _rows) or 1)})

        with _tab_e:
            _edges = sorted(_lg.all_edges(), key=lambda e: e['weight'], reverse=True)
            _mw    = st.slider("Min weight", 0.0, 1.0, 0.05, 0.01, label_visibility='collapsed')
            _edges = [e for e in _edges if e['weight'] >= _mw][:200]
            _erows = [{'A': _lg._g.nodes[e['source']].get('text', e['source']),
                       'B': _lg._g.nodes[e['target']].get('text', e['target']),
                       'weight': round(e['weight'], 4)} for e in _edges]
            if _erows:
                st.dataframe(pd.DataFrame(_erows), width='stretch', hide_index=True,
                             column_config={'weight': st.column_config.ProgressColumn('weight', max_value=1.0)})

        with _tab_cl:
            try:
                _comms = sorted(nx.community.greedy_modularity_communities(_lg._g), key=len, reverse=True)
                st.caption(f"{len(_comms)} clusters")
                for _ci, _comm in enumerate(_comms[:15]):
                    _mems = sorted(_comm, key=lambda n: _lg._g.nodes[n].get('activation_count', 0), reverse=True)
                    _lbl  = _lg._g.nodes[_mems[0]]['text'] if _mems else f'cluster {_ci}'
                    with st.expander(f"{_lbl}  ·  {len(_mems)} concepts"):
                        _cols = st.columns(3)
                        for _i, _nid in enumerate(_mems):
                            _cols[_i % 3].caption(_lg._g.nodes[_nid]['text'])
            except Exception as _e:
                st.caption(f"Cluster error: {_e}")

        with _tab_am:
            _slog = os.path.join(str(ROOT), 'logs', 'ambiguity_scores.jsonl')
            if os.path.exists(_slog):
                _ents = [json.loads(l) for l in open(_slog, encoding='utf-8') if l.strip()]
                _ents.reverse()
                _lf = st.multiselect("Level", ['low', 'medium', 'high'], default=['low', 'medium', 'high'])
                for _e in [e for e in _ents if e.get('level') in _lf][:50]:
                    with st.container(border=True):
                        _c1, _c2 = st.columns([5, 1])
                        _c1.caption(_e.get('input_text', '')[:100])
                        _c2.caption(f"`{_e.get('score', 0):.2f}`")
            else:
                st.caption("No ambiguity log yet.")


# ── Auto-refresh every 4 seconds (in-session rerun — keeps WebSocket alive) ──
import time as _time
_time.sleep(4)
st.rerun()
