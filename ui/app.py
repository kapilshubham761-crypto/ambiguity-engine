"""
Ambiguity Engine — Streamlit entry point.

Node position in the UI tree:
    app.py  ◄─── THIS FILE
        ├─ Boot splash        (plays once per browser session)
        ├─ Status detection   (reads data/ files to determine engine state)
        ├─ Sidebar            (dot + word + elapsed time + Stop/Resume)
        └─ Navigation         (routes to _pages/)
"""

import os
import sys
import json
import time
import streamlit as st
from datetime import datetime, timezone
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'components'))

# Pre-initialize torch in the main thread before any page code runs.
# Without this, the Runner page triggers torch's first import mid-execution
# which causes an internal circular import (torch/__init__.py → torch.export
# → torch._higher_order_ops → torch.utils._debug_mode → torch.library not
# yet set). Importing here ensures torch is fully ready before pg.run().
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
    import torch
except Exception:
    pass


# ======================================================================== #
# Page config                                                               #
# ======================================================================== #

st.set_page_config(
    page_title="Ambiguity Engine",
    page_icon=Image.open(os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')),
    layout="wide",
    initial_sidebar_state="expanded",
)

_LOGO = os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')
st.logo(_LOGO, size="large")

_ROOT = os.path.join(os.path.dirname(__file__), '..')


# ======================================================================== #
# Boot splash                                                               #
# Plays for 4 seconds on the first page load of a browser session.         #
# sessionStorage key 'ae_booted_v1' prevents replay on navigation.         #
# ======================================================================== #

st.markdown("""
<style>
#ae-boot {
    position: fixed; inset: 0; z-index: 999999;
    background: #0a0a0a;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 18px;
    animation: ae-boot-fade 0.8s ease-out 4s forwards;
    pointer-events: none;
}
@keyframes ae-boot-fade {
    to { opacity: 0; visibility: hidden; }
}
.ae-boot-glyph {
    font-size: 3.2rem; color: #4a9eff;
    animation: ae-boot-pulse 0.9s ease-in-out infinite alternate;
}
@keyframes ae-boot-pulse {
    from { opacity: 0.4; transform: scale(0.88); text-shadow: 0 0 0px #4a9eff; }
    to   { opacity: 1.0; transform: scale(1.04); text-shadow: 0 0 28px #4a9eff; }
}
.ae-boot-title {
    font-family: 'Consolas', monospace;
    font-size: 1.05rem; letter-spacing: 0.32em;
    color: #cccccc; text-transform: uppercase;
}
.ae-boot-sub {
    font-family: 'Consolas', monospace;
    font-size: 0.7rem; letter-spacing: 0.22em;
    color: #555; text-transform: uppercase;
    animation: ae-boot-blink 0.7s step-end infinite;
}
@keyframes ae-boot-blink {
    0%, 100% { opacity: 1; } 50% { opacity: 0; }
}
.ae-boot-bar-wrap {
    width: 180px; height: 2px;
    background: #1a1a1a; border-radius: 2px; overflow: hidden;
}
.ae-boot-bar {
    height: 100%; width: 0%;
    background: linear-gradient(90deg, #4a9eff, #44ff88);
    border-radius: 2px;
    animation: ae-boot-fill 3.8s cubic-bezier(0.4,0,0.2,1) forwards;
}
@keyframes ae-boot-fill {
    0%   { width:  0%; }
    60%  { width: 75%; }
    90%  { width: 95%; }
    100% { width:100%; }
}
</style>

<div id="ae-boot">
  <div class="ae-boot-glyph">◈</div>
  <div class="ae-boot-title">Ambiguity Engine</div>
  <div class="ae-boot-bar-wrap"><div class="ae-boot-bar"></div></div>
  <div class="ae-boot-sub">waking up_</div>
</div>

<script>
(function() {
    var key = 'ae_booted_v1';
    var el  = document.getElementById('ae-boot');
    if (!el) return;
    if (sessionStorage.getItem(key)) {
        el.style.display = 'none';
    } else {
        sessionStorage.setItem(key, '1');
    }
})();
</script>
""", unsafe_allow_html=True)

# Global CSS — injected from main page so it always applies
st.markdown("""
<style>
body, .stApp { background: #070709 !important; }
*, *::before, *::after { font-family: 'Consolas', 'Courier New', monospace !important; }

/* Restore Material Symbols font — must beat * with higher specificity + liga enabled */
.material-symbols-rounded,
.material-symbols-outlined,
.material-icons,
span[class*="material-symbols"],
span[class*="material-icons"],
[data-testid="stExpanderToggleIcon"] span,
[data-testid="stExpanderToggleIcon"] > *,
[data-testid="stSidebarCollapseButton"] span,
[data-baseweb="icon"] span {
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons', sans-serif !important;
    font-weight: normal !important;
    font-style: normal !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    white-space: nowrap !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-smoothing: antialiased !important;
}

/* Sidebar — always visible, no collapse */
[data-testid="stSidebar"] {
    background: #09090f !important;
    border-right: 1px solid #2a2a48 !important;
    min-width: 240px !important;
    transform: none !important;
    visibility: visible !important;
}
[data-testid="stSidebarContent"] { background: #09090f !important; }

/* Hide the collapse/expand toggle button — cover all Streamlit versions */
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
section[data-testid="stSidebarCollapsedControl"],
button[data-testid="stBaseButton-headerNoPadding"],
button[title="Close sidebar"],
button[title="Open sidebar"],
button[aria-label="Close sidebar"],
button[aria-label="Open sidebar"],
[data-testid="stSidebar"] > div:first-child > button {
    display: none !important;
    visibility: hidden !important;
    width: 0 !important;
    height: 0 !important;
    overflow: hidden !important;
    pointer-events: none !important;
}

/* Nav links */
[data-testid="stSidebarNav"] { padding: 4px 0 !important; }
[data-testid="stSidebarNavLink"] {
    border-radius: 3px !important;
    margin: 1px 8px !important;
    padding: 7px 12px !important;
    color: #6868a0 !important;
    font-size: 13px !important;
    letter-spacing: 0.06em !important;
    background: transparent !important;
    text-decoration: none !important;
}
[data-testid="stSidebarNavLink"]:hover {
    background: #111128 !important;
    color: #9898c8 !important;
}
[data-testid="stSidebarNavLink"][aria-current="page"] {
    background: #14142a !important;
    color: #b0b0d8 !important;
    border-left: 2px solid #4a9eff !important;
}
[data-testid="stSidebarNavLink"] span { color: inherit !important; font-size: 13px !important; }

/* Sidebar buttons */
[data-testid="stSidebar"] button {
    background: #0a0a18 !important;
    border: 1px solid #222238 !important;
    color: #7878a8 !important;
    font-size: 12px !important;
    letter-spacing: 0.08em !important;
}
[data-testid="stSidebar"] button:hover { border-color: #4a4a70 !important; color: #b0b0d8 !important; }
[data-testid="stSidebar"] button[kind="primary"] {
    background: #0d1a2a !important; border-color: #4a9eff !important; color: #4a9eff !important;
}
</style>
""", unsafe_allow_html=True)


# ======================================================================== #
# Live feed bar — pinned to top, shows latest AutoLearner entry            #
# ======================================================================== #

_lv_title = _lv_source = _lv_concepts = _lv_sentence = _lv_progress = ''
_lv_live = False

# Currently-reading.json: written per-sentence inside the fetch loop — most live signal
try:
    _cr = json.loads(open(os.path.join(_ROOT, 'data', 'currently_reading.json'), encoding='utf-8').read())
    _lv_title    = _cr.get('title', '')
    _lv_source   = _cr.get('source', '')
    _lv_sentence = _cr.get('sentence', '')
    _lv_progress = _cr.get('progress', '')
    _lv_live     = True
except Exception:
    pass

# Fall back to last completed entry if currently_reading is absent
if not _lv_title:
    try:
        _lf_path = os.path.join(_ROOT, 'data', 'live_feed.jsonl')
        with open(_lf_path, encoding='utf-8') as _lf:
            for _ln in _lf:
                _ln = _ln.strip()
                if _ln:
                    _le = json.loads(_ln)
                    _lv_title    = _le.get('title', '')
                    _lv_source   = _le.get('source', '')
                    _lv_concepts = _le.get('concepts', '')
    except Exception:
        pass

if _lv_title:
    _lv_progress_part = (
        f'<span style="color:#404060;margin-left:8px;font-size:10px">[{_lv_progress}]</span>'
        if _lv_progress else ''
    )
    _lv_sentence_part = (
        f'<span style="color:#6868a0;margin-left:10px;font-style:italic">{_lv_sentence[:100]}{"…" if len(_lv_sentence) > 100 else ""}</span>'
        if _lv_sentence else ''
    )
    _lv_conc_part = (f'<span style="color:#333355;margin-left:12px">{_lv_concepts} concepts</span>'
                     if _lv_concepts else '')
    _lv_display = (
        f'<span style="color:#505078;margin-right:8px;text-transform:uppercase;'
        f'font-size:10px;letter-spacing:0.12em">reading</span>'
        f'<span style="color:#b0b0d8;margin-right:8px">{_lv_title[:120]}{"…" if len(_lv_title) > 120 else ""}</span>'
        f'<span style="color:#404060">({_lv_source})</span>'
        f'{_lv_progress_part}'
        f'{_lv_sentence_part}'
        f'{_lv_conc_part}'
    )
else:
    _lv_display = '<span style="color:#303050">no feed yet — start the learner</span>'

st.markdown(f"""
<style>
#ae-livebar {{
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 99999;
    height: 26px;
    background: #050507;
    border-bottom: 1px solid #141420;
    display: flex;
    align-items: center;
    gap: 0;
    padding: 0 16px;
    font-family: 'Consolas', monospace;
    font-size: 12px;
    overflow: hidden;
    white-space: nowrap;
}}
#ae-livebar-dot {{
    width: 6px; height: 6px; border-radius: 50%;
    background: {'#44ff88' if _lv_title else '#303050'};
    margin-right: 10px;
    flex-shrink: 0;
    animation: ae-pulse 1.6s ease-in-out infinite;
}}
/* Push all page content below the bar */
.stApp > header {{ top: 26px !important; }}
.block-container {{ padding-top: 2.5rem !important; }}
[data-testid="stSidebar"] {{ top: 26px !important; height: calc(100vh - 26px) !important; }}
</style>
<div id="ae-livebar">
  <div id="ae-livebar-dot"></div>
  {_lv_display}
</div>
""", unsafe_allow_html=True)


# ======================================================================== #
# Status detection — delegated to ui/components/status.py                  #
# ======================================================================== #

from status import status_word_and_colour, render_fetch_elapsed

word, colour = status_word_and_colour()
_paused      = (word == 'paused')


# Fetch elapsed computed inline for the sidebar HTML block below
_fetch_elapsed_str = ''
_thinking_line     = ''

try:
    _fs = json.load(open(os.path.join(_ROOT, 'data', 'fetch_status.json'), encoding='utf-8'))
    if _fs.get('fetching') and _fs.get('started_at') and not _paused:
        _started = datetime.fromisoformat(_fs['started_at'])
        if _started.tzinfo is None:
            _started = _started.replace(tzinfo=timezone.utc)
        _elapsed = int((datetime.now(tz=timezone.utc) - _started).total_seconds())
        _m, _s   = divmod(_elapsed, 60)
        _fetch_elapsed_str = f"{_m}m {_s:02d}s" if _m else f"{_s}s"
        _THOUGHTS = ["searching sources", "reading content",
                     "extracting concepts", "updating graph", "reinforcing memory"]
        _t         = int(time.time())
        _thinking_line = f"{_THOUGHTS[(_t // 3) % len(_THOUGHTS)]} {'·  ·· ···'.split()[_t % 3]}"
except Exception:
    pass


# ======================================================================== #
# Sidebar                                                                   #
# ├─ Stop / Resume button (writes paused.txt directly — no Teacher needed) #
# └─ Animated status dot + word + elapsed time + thinking line             #
# ======================================================================== #

_paused_file = os.path.join(_ROOT, 'data', 'paused.txt')

with st.sidebar:
    if _paused:
        if st.button("Resume", width='stretch', type="primary"):
            with open(_paused_file, 'w') as _f:
                _f.write('0')
            st.rerun()
    else:
        if st.button("Stop all", width='stretch'):
            os.makedirs(os.path.dirname(_paused_file), exist_ok=True)
            with open(_paused_file, 'w') as _f:
                _f.write('1')
            st.rerun()

_elapsed_html  = (
    f'<span style="color:#777;margin-left:6px">{_fetch_elapsed_str}</span>'
    if _fetch_elapsed_str else ''
)
_thinking_html = (
    f'<div class="ae-thinking">{_thinking_line}</div>'
    if _thinking_line else ''
)

st.sidebar.markdown(
    f"""
    <style>
    /* Slim loading bar that runs across the top on every page transition */
    #ae-topbar {{
        position: fixed; top: 0; left: 0; z-index: 99998;
        height: 2px; width: 0%;
        background: linear-gradient(90deg, #4a9eff, #44ff88);
        animation: ae-topbar-run 1.1s cubic-bezier(0.4,0,0.2,1) forwards;
        pointer-events: none;
    }}
    @keyframes ae-topbar-run {{
        0%   {{ width:  0%; opacity: 1; }}
        80%  {{ width: 92%; opacity: 1; }}
        100% {{ width:100%; opacity: 0; }}
    }}
    @keyframes ae-pulse {{
        0%, 100% {{ opacity: 1;    transform: scale(1);    }}
        50%      {{ opacity: 0.45; transform: scale(0.72); }}
    }}
    .ae-status {{
        display: flex; align-items: center; gap: 7px;
        padding: 6px 12px 4px 12px;
        font-size: 0.78em; color: #bbb; letter-spacing: 0.04em;
    }}
    .ae-dot {{
        width: 8px; height: 8px; border-radius: 50%;
        background: {colour};
        animation: ae-pulse 1.6s ease-in-out infinite;
        flex-shrink: 0;
    }}
    .ae-thinking {{
        padding: 0 12px 6px 27px;
        font-size: 0.72em; color: #666; font-style: italic;
        letter-spacing: 0.03em;
    }}
    </style>
    <div id="ae-topbar"></div>
    <div class="ae-status">
      <span class="ae-dot"></span>
      <span>{word}</span>{_elapsed_html}
    </div>
    {_thinking_html}
    """,
    unsafe_allow_html=True,
)

# ── V3 cognitive chip (goal + mode) ──────────────────────────────────────────
_goal_html = ''
try:
    _cog_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'cog_status.json')
    with open(_cog_path, encoding='utf-8') as _cf:
        _cog = json.load(_cf)
    _goal = _cog.get('goal', 'explore').replace('_', ' ')
    _mode = _cog.get('mode', 'associative')
    _MODE_CHIP_COLOUR = {
        'focused':      '#f59e0b',
        'exploratory':  '#3b82f6',
        'associative':  '#8b5cf6',
        'exploitative': '#ef4444',
        'reflective':   '#10b981',
    }
    _mc = _MODE_CHIP_COLOUR.get(_mode, '#6b7280')
    _goal_html = f"""
    <style>
    .ae-cog-chip {{
        display:flex; align-items:center; gap:6px;
        padding: 5px 12px; margin:4px 0;
        font-size:0.73em; color:#bbb; border-radius:4px;
        background:#11111a; border:1px solid #1e1e2e;
    }}
    .ae-cog-dot {{ width:6px;height:6px;border-radius:50%;background:{_mc};flex-shrink:0; }}
    .ae-cog-label {{ font-size:0.68em;color:#555;text-transform:uppercase;letter-spacing:0.07em; }}
    </style>
    <div class="ae-cog-chip">
      <div class="ae-cog-dot"></div>
      <div>
        <div class="ae-cog-label">mode / goal</div>
        <div style="color:{_mc}">{_mode}</div>
      </div>
      <div style="margin-left:auto;text-align:right">
        <div class="ae-cog-label">drive</div>
        <div style="color:#a78bfa">{_goal}</div>
      </div>
    </div>
    """
except Exception:
    pass

if _goal_html:
    st.sidebar.markdown(_goal_html, unsafe_allow_html=True)


# ======================================================================== #
# Navigation                                                                #
# Pages use underscore prefix (_pages/) so Streamlit doesn't               #
# auto-discover them — navigation is explicit here.                        #
# ======================================================================== #

pages = [
    st.Page("_pages/1_state.py",       title="Core"),
    st.Page("_pages/3_runner.py",      title="Runner"),
    st.Page("_pages/0_meta_state.py",  title="Meta-State"),
    st.Page("_pages/10_cognition.py",  title="Cognition"),
    st.Page("_pages/12_ytp.py",        title="YTP"),
    st.Page("_pages/11_config.py",     title="Settings"),
]

pg = st.navigation(pages)
pg.run()
