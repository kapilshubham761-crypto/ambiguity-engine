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
import json
import time
import streamlit as st
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'components'))


# ======================================================================== #
# Page config                                                               #
# ======================================================================== #

st.set_page_config(
    page_title="Ambiguity Engine",
    page_icon="◈",
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
                     "scoring readability", "extracting sentences", "saving to queue"]
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
        if st.button("▶️ Resume", use_container_width=True, type="primary"):
            with open(_paused_file, 'w') as _f:
                _f.write('0')
            st.rerun()
    else:
        if st.button("⏹ Stop all", use_container_width=True):
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


# ======================================================================== #
# Navigation                                                                #
# Pages use underscore prefix (_pages/) so Streamlit doesn't               #
# auto-discover them — navigation is explicit here.                        #
# ======================================================================== #

pages = [
    st.Page("_pages/1_state.py",       title="State",       icon="🔮"),
    st.Page("_pages/2_graph.py",       title="Graph",       icon="🕸️"),
    st.Page("_pages/3_runner.py",      title="Runner",      icon="▶️"),
    st.Page("_pages/4_timeline.py",    title="Timeline",    icon="📅"),
    st.Page("_pages/5_ab.py",          title="A / B",       icon="🔀"),
    st.Page("_pages/6_discover.py",    title="Learn",       icon="📚"),
    st.Page("_pages/7_learnings.py",   title="Learnings",   icon="📖"),
    st.Page("_pages/9_report_card.py", title="Report Card", icon="📊"),
]

pg = st.navigation(pages)
pg.run()
