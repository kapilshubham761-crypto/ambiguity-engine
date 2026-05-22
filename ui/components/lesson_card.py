"""
UI Component — Lesson Card
==========================
Single responsibility: render one lesson item from the queue.

Exported functions
------------------
render_lesson_card(lesson, teacher, graph, key_suffix)
    Renders a bordered card with title, source badge, preview text,
    and Accept / Reject buttons.  Calls teacher.accept/reject on click.
"""

from __future__ import annotations

import streamlit as st

_SOURCE_ICON = {
    'wikipedia':   '📖',
    'simple_wiki': '🟦',
    'arxiv':       '🔬',
    'gutenberg':   '📚',
    'reddit':      '💬',
    'openalex':    '🎓',
    'web':         '🌐',
}

_STAGE_COLOUR = {
    0: '#88ccff', 1: '#aaddff', 2: '#88ffcc', 3: '#aaffee',
    4: '#ffee88', 5: '#ffcc88', 6: '#ff9988', 7: '#ff88cc',
}


def render_lesson_card(lesson: dict, teacher, graph,
                       key_suffix: str = '') -> str | None:
    """
    Render a single lesson card with Accept / Reject controls.

    Returns 'accepted', 'rejected', or None (no action taken this render).
    """
    lid    = lesson['id']
    src    = lesson.get('source', 'web')
    stage  = lesson.get('stage', 0)
    icon   = _SOURCE_ICON.get(src, '🌐')
    colour = _STAGE_COLOUR.get(stage, '#aaa')
    is_hw  = lesson.get('is_homework', False)

    with st.container(border=True):
        h1, h2 = st.columns([7, 1])
        h1.markdown(
            f"{icon} **{lesson.get('title', '—')[:90]}**"
            f"{'  🏠 *hw*' if is_hw else ''}"
        )
        h2.markdown(
            f"<span style='background:{colour}22; color:{colour}; "
            f"padding:2px 7px; border-radius:4px; font-size:0.75em;'>"
            f"{lesson.get('stage_label','')[:10]}</span>",
            unsafe_allow_html=True,
        )

        if lesson.get('preview'):
            st.caption(lesson['preview'][:200])

        c1, c2, _ = st.columns([1, 1, 4])
        action = None
        if c1.button("✅ Accept", key=f"acc_{lid}{key_suffix}", use_container_width=True):
            with st.spinner("Fetching + extracting…"):
                n = teacher.accept(lid, graph)
            st.toast(f"Absorbed {n} sentences", icon="🧠")
            action = 'accepted'
        if c2.button("❌ Reject", key=f"rej_{lid}{key_suffix}", use_container_width=True):
            teacher.reject(lid)
            action = 'rejected'

    return action
