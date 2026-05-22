import os
import json
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

st.sidebar.markdown(
    f"""
    <style>
    @keyframes ae-pulse {{
        0%,100% {{ opacity:1; transform:scale(1); }}
        50%      {{ opacity:0.45; transform:scale(0.72); }}
    }}
    .ae-status {{
        display:flex; align-items:center; gap:7px;
        padding:6px 12px 8px 12px;
        font-size:0.78em; color:#bbb; letter-spacing:0.04em;
    }}
    .ae-dot {{
        width:8px; height:8px; border-radius:50%;
        background:{colour};
        animation:ae-pulse 1.6s ease-in-out infinite;
        flex-shrink:0;
    }}
    </style>
    <div class="ae-status">
      <span class="ae-dot"></span>
      <span>{word}</span>
    </div>
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
