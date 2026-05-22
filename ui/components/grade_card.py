"""
UI Component — Grade Card
=========================
Single responsibility: render report card grades and progress bars.

Exported functions
------------------
render_grade_row(card)          six metric columns (Overall + 5 sub-grades)
render_syllabus(graph, teacher) curriculum milestones with progress bars
render_homework_board(teacher)  pending + completed homework assignments
"""

from __future__ import annotations

import streamlit as st

GRADE_COLOUR = {
    'A+': '#00ff88', 'A': '#44ff88',
    'B+': '#88ffaa', 'B': '#aaffcc',
    'C+': '#ffee44', 'C': '#ffcc44',
    'D':  '#ff8844', 'F': '#ff4444',
}

_STAGE_BORDER = {
    True:  '#4a9eff',   # current stage
    False: '#44ff88',   # completed stage
}


def render_grade_row(card: dict) -> None:
    """Render six metric columns from a report card dict."""
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Overall",      card['overall_grade'])
    m2.metric("Breadth",      card['grade_breadth'])
    m3.metric("Depth",        card['grade_depth'])
    m4.metric("Activation",   card['grade_activation'])
    m5.metric("Calibration",  card['grade_calibration'])
    m6.metric("Velocity",     card['grade_velocity'])


def render_syllabus(graph, teacher) -> None:
    """Render curriculum milestones with progress bars for the current stage."""
    from assessor import STAGE_TARGETS

    current_stage = teacher._current_stage()

    for stage_i, target in STAGE_TARGETS.items():
        done    = stage_i < current_stage
        current = stage_i == current_stage

        icon   = "✅" if done else ("📖" if current else "🔒")
        colour = "#4a9eff" if current else ("#44ff88" if done else "#555")

        st.markdown(
            f"<div style='padding:10px 16px; margin:4px 0; border-radius:8px; "
            f"border:1px solid {colour}44; background:{colour}11;'>"
            f"<b style='color:{colour};'>{icon} {target['label']}</b>"
            f"{'  <span style=\"background:#4a9eff22; padding:2px 8px; border-radius:4px; font-size:0.8em;\">CURRENT</span>' if current else ''}"
            f"<br><span style='color:#aaa; font-size:0.85em;'>"
            f"Target: {target['nodes']} concepts · {target['edges']} connections"
            f"{'  —  You have: ' + str(graph.node_count) + ' / ' + str(graph.edge_count) if current else ''}"
            f"</span></div>",
            unsafe_allow_html=True,
        )
        if current:
            st.progress(
                min(graph.node_count / target['nodes'], 1.0),
                text=f"{graph.node_count}/{target['nodes']} concepts",
            )


def render_homework_board(teacher) -> None:
    """Render the homework assignment board (pending + completed)."""
    hw = teacher.homework
    if not hw:
        st.info("No homework yet — run an assessment to generate assignments.")
        return

    done_hw    = [h for h in hw if h['status'] == 'done']
    pending_hw = [h for h in hw if h['status'] == 'pending']

    p1, p2 = st.columns(2)
    p1.metric("Pending",   len(pending_hw))
    p2.metric("Completed", len(done_hw))

    st.subheader("Pending assignments")
    for hw_item in pending_hw:
        cov = hw_item.get('coverage', 0)
        st.markdown(
            f"📝 **{hw_item['topic']}** &nbsp; "
            f"coverage: `{cov:.0%}` &nbsp; "
            f"<span style='color:#ff8844;'>{'⚠ weak' if cov < 0.2 else ''}</span>",
            unsafe_allow_html=True,
        )

    if done_hw:
        st.subheader("Completed ✅")
        for hw_item in done_hw:
            st.markdown(f"✅ ~~{hw_item['topic']}~~ &nbsp; `{hw_item.get('coverage',0):.0%}`")
