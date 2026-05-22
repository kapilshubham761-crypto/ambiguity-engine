import os
import json
import time
import streamlit as st
from datetime import datetime, timezone

st.set_page_config(
    page_title="Ambiguity Engine",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

_LOGO = os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')
st.logo(_LOGO, size="large")

# ------------------------------------------------------------------ #
# Sidebar status indicator                                             #
# ------------------------------------------------------------------ #

_ROOT = os.path.join(os.path.dirname(__file__), '..')

def _status() -> tuple[str, str]:
    """Return (word, hex_colour) describing what the system is doing."""
    # Check paused flag first
    try:
        p = os.path.join(_ROOT, 'data', 'paused.txt')
        if open(p).read().strip() == '1':
            return 'paused', '#ff4444'
    except Exception:
        pass

    # Check if teacher is actively fetching (queue is low)
    try:
        q = json.load(open(os.path.join(_ROOT, 'data', 'teacher_queue.json'), encoding='utf-8'))
        queue_size = len(q) if isinstance(q, list) else 0
    except Exception:
        queue_size = -1

    # Check recency of last accepted lesson
    try:
        stats = json.load(open(os.path.join(_ROOT, 'data', 'teacher_stats.json'), encoding='utf-8'))
        sessions = stats.get('sessions', [])
        if sessions:
            last = sessions[-1]
            last_dt = datetime.fromisoformat(last['ts'])
            age_s = (datetime.now(tz=timezone.utc) - last_dt).total_seconds()
            if age_s < 300 and last['action'] == 'accept':
                return 'learning', '#44ff88'
            if age_s < 300 and last['action'] == 'reject':
                return 'filtering', '#ffaa44'
    except Exception:
        pass

    # Check if report card check-in is running
    try:
        cards = json.load(open(os.path.join(_ROOT, 'data', 'report_cards.json'), encoding='utf-8'))
        if cards:
            last_ts = cards[-1]['timestamp']
            last_dt = datetime.fromisoformat(last_ts)
            age_s = (datetime.now(tz=timezone.utc) - last_dt).total_seconds()
            if age_s < 120:
                return 'assessing', '#bb88ff'
    except Exception:
        pass

    if queue_size == 0:
        return 'fetching', '#4a9eff'
    if queue_size > 0 and queue_size < 4:
        return 'fetching', '#4a9eff'

    return 'idle', '#888888'

word, colour = _status()
_paused = (word == 'paused')

# ------------------------------------------------------------------ #
# Fetch status — elapsed time + thinking line                         #
# ------------------------------------------------------------------ #

_fetch_status_path = os.path.join(_ROOT, 'data', 'fetch_status.json')
_fetch_elapsed_str = ''
_thinking_line     = ''

try:
    _fs = json.load(open(_fetch_status_path, encoding='utf-8'))
    if _fs.get('fetching') and _fs.get('started_at') and not _paused:
        _started = datetime.fromisoformat(_fs['started_at'])
        if _started.tzinfo is None:
            _started = _started.replace(tzinfo=timezone.utc)
        _elapsed = int((datetime.now(tz=timezone.utc) - _started).total_seconds())
        _m, _s   = divmod(_elapsed, 60)
        _fetch_elapsed_str = f"{_m}m {_s:02d}s" if _m else f"{_s}s"

        _THOUGHTS = [
            "searching sources",
            "reading content",
            "scoring readability",
            "extracting sentences",
            "saving to queue",
        ]
        _dot_frames = ["·  ", "·· ", "···"]
        _t          = int(time.time())
        _thought    = _THOUGHTS[(_t // 3) % len(_THOUGHTS)]
        _dots       = _dot_frames[_t % len(_dot_frames)]
        _thinking_line = f"{_thought} {_dots}"
except Exception:
    pass

# Stop / Resume button in sidebar
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

_elapsed_html = (
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
    @keyframes ae-pulse {{
        0%,100% {{ opacity:1; transform:scale(1); }}
        50%      {{ opacity:0.45; transform:scale(0.72); }}
    }}
    .ae-status {{
        display:flex; align-items:center; gap:7px;
        padding:6px 12px 4px 12px;
        font-size:0.78em; color:#bbb; letter-spacing:0.04em;
    }}
    .ae-dot {{
        width:8px; height:8px; border-radius:50%;
        background:{colour};
        animation:ae-pulse 1.6s ease-in-out infinite;
        flex-shrink:0;
    }}
    .ae-thinking {{
        padding:0 12px 6px 27px;
        font-size:0.72em; color:#666; font-style:italic;
        letter-spacing:0.03em;
    }}
    </style>
    <div class="ae-status">
      <span class="ae-dot"></span>
      <span>{word}</span>{_elapsed_html}
    </div>
    {_thinking_html}
    """,
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ #
# Navigation                                                           #
# ------------------------------------------------------------------ #

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
