"""
UI Component — Status
=====================
Single responsibility: render engine status indicators.

Exported functions
------------------
render_status_dot(teacher)   sidebar animated dot + stop/resume button
render_fetch_elapsed()       sidebar fetch timer line
status_word_and_colour()     returns (word, hex) — used by sidebar and overlay
"""

from __future__ import annotations

import json
import os
import time

import streamlit as st

_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')


# ======================================================================== #
# Status helpers                                                            #
# ======================================================================== #

def status_word_and_colour() -> tuple[str, str]:
    """
    Derive a single-word status and HEX colour from the data/ files.
    Does NOT require a Teacher instance — reads files directly so it works
    even if the Teacher thread hasn't started yet.
    """
    try:
        paused = open(os.path.join(_ROOT, 'data', 'paused.txt')).read().strip() == '1'
        if paused:
            return 'paused', '#ff8844'
    except Exception:
        pass

    try:
        fs = json.load(open(os.path.join(_ROOT, 'data', 'fetch_status.json')))
        if fs.get('fetching'):
            return 'fetching', '#4a9eff'
    except Exception:
        pass

    return 'running', '#44ff88'


def render_fetch_elapsed() -> None:
    """Show elapsed fetch time + a thinking line in the sidebar."""
    try:
        fs = json.load(open(os.path.join(_ROOT, 'data', 'fetch_status.json')))
    except Exception:
        return

    if not fs.get('fetching') or not fs.get('started_at'):
        return

    from datetime import datetime, timezone
    started = datetime.fromisoformat(fs['started_at'])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = int((datetime.now(tz=timezone.utc) - started).total_seconds())

    _THINKING = [
        "Reading sources…", "Sifting vocabulary…", "Mapping concepts…",
        "Connecting ideas…", "Weighing relevance…", "Cross-referencing…",
    ]
    thinking = _THINKING[(elapsed // 4) % len(_THINKING)]

    st.sidebar.caption(f"⏱ {elapsed}s · {thinking}")


def render_status_dot() -> None:
    """
    Render the animated status dot and Stop/Resume button in the sidebar.
    Writes paused.txt directly — no Teacher instance required.
    Call from app.py or any page that renders sidebar controls.
    """
    word, colour = status_word_and_colour()
    is_paused = (word == 'paused')

    _paused_file = os.path.join(_ROOT, 'data', 'paused.txt')
    if is_paused:
        if st.sidebar.button("▶️ Resume", use_container_width=True, type="primary"):
            os.makedirs(os.path.dirname(_paused_file), exist_ok=True)
            open(_paused_file, 'w').write('0')
            st.rerun()
    else:
        if st.sidebar.button("⏹ Stop all", use_container_width=True):
            os.makedirs(os.path.dirname(_paused_file), exist_ok=True)
            open(_paused_file, 'w').write('1')
            st.rerun()

    dot_html = f"""
    <style>
    @keyframes ae-pulse {{
        0%, 100% {{ opacity: 1;    transform: scale(1);    }}
        50%      {{ opacity: 0.45; transform: scale(0.72); }}
    }}
    .ae-dot {{
        display:inline-block; width:10px; height:10px;
        border-radius:50%; background:{colour};
        animation: {'none' if is_paused else 'ae-pulse 1.6s ease-in-out infinite'};
        margin-right:6px; vertical-align:middle;
    }}
    </style>
    <span class="ae-dot"></span>
    <span style="color:#ccc; font-size:0.85em;">{word}</span>
    """
    st.sidebar.markdown(dot_html, unsafe_allow_html=True)
    render_fetch_elapsed()
