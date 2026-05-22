"""9 — Report Card: track the AGI's cognitive development over time."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import json
from datetime import datetime, date, timezone
import streamlit as st
import pandas as pd

from graph import SemanticGraph
from report_card import load_cards, assess, save_card, BIRTHDATE, STAGE_TARGETS
from teacher import Teacher

st.title("📊 Report Card")
st.caption("Ambiguity Engine's academic record — assessed every 3 hours by the Teacher.")

# ------------------------------------------------------------------ #
# Singletons                                                           #
# ------------------------------------------------------------------ #

@st.cache_resource(show_spinner=False)
def load_graph():
    return SemanticGraph()

@st.cache_resource(show_spinner=False)
def load_teacher() -> Teacher:
    t = Teacher()
    t.start(graph=load_graph())
    return t

g = load_graph()
t = load_teacher()

# ------------------------------------------------------------------ #
# Birth certificate                                                    #
# ------------------------------------------------------------------ #

bd        = date.fromisoformat(BIRTHDATE)
age_days  = (date.today() - bd).days
age_str   = f"{age_days} days old" if age_days > 0 else "Born today"

st.markdown(f"""
<div style='background:linear-gradient(135deg,#1a1a2e,#16213e);
            border:1px solid #4a9eff44; border-radius:12px;
            padding:20px 28px; margin-bottom:20px;'>
  <span style='font-size:2em;'>🎂</span>
  <span style='font-size:1.4em; font-weight:700; color:#e0e0ff;
               margin-left:12px;'>Ambiguity Engine</span>
  <span style='color:#4a9eff; margin-left:10px;'>· Born {BIRTHDATE}</span>
  <br>
  <span style='color:#aaa; margin-left:48px;'>{age_str}
  &nbsp;·&nbsp; {g.node_count} concepts &nbsp;·&nbsp; {g.edge_count} connections</span>
</div>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------ #
# Current grade + run check-in manually                                #
# ------------------------------------------------------------------ #

cards = load_cards()

col_grade, col_btn = st.columns([3, 1])
with col_btn:
    st.write("")
    if st.button("📋 Run assessment now", use_container_width=True, type="primary"):
        with st.spinner("Assessing…"):
            card = t.check_in(g)
        st.cache_resource.clear()
        st.rerun()
    st.caption(f"Next auto check-in: {t.next_checkin_in()}")

if not cards:
    with col_grade:
        st.info("No report cards yet. Click 'Run assessment now' to generate the first one.")
    st.stop()

latest = cards[-1]

GRADE_COLOUR = {
    'A+': '#00ff88', 'A': '#44ff88',
    'B+': '#88ffaa', 'B': '#aaffcc',
    'C+': '#ffee44', 'C': '#ffcc44',
    'D':  '#ff8844', 'F': '#ff4444',
}

with col_grade:
    g_colour = GRADE_COLOUR.get(latest['overall_grade'], '#aaa')
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Overall", latest['overall_grade'])
    m2.metric("Breadth",    latest['grade_breadth'])
    m3.metric("Depth",      latest['grade_depth'])
    m4.metric("Activation", latest['grade_activation'])
    m5.metric("Calibration",latest['grade_calibration'])
    m6.metric("Velocity",   latest['grade_velocity'])

st.markdown(f"> 🎓 **Teacher's comment:** *{latest['narrative']}*")

st.divider()

# ------------------------------------------------------------------ #
# Tabs                                                                 #
# ------------------------------------------------------------------ #

tab_progress, tab_syllabus, tab_homework, tab_history = st.tabs([
    "📈 Progress", "📚 Syllabus", "📝 Homework", "🗂 History"
])

# ------------------------------------------------------------------ #
# Tab 1 — Progress charts                                              #
# ------------------------------------------------------------------ #
with tab_progress:
    if len(cards) < 2:
        st.info("Need at least 2 assessments to show progress. Run another check-in in a few hours.")
    else:
        df = pd.DataFrame(cards)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp')

        st.subheader("Knowledge growth")
        st.line_chart(df[['node_count', 'edge_count']], height=220)

        st.subheader("GPA over time")
        st.line_chart(df[['gpa']], height=180)

        st.subheader("Metrics over time")
        # Map letter grades to GPA for charting
        _gpa = {'A+':4.3,'A':4.0,'B+':3.3,'B':3.0,'C+':2.3,'C':2.0,'D':1.0,'F':0.0}
        for col in ['grade_breadth','grade_depth','grade_activation','grade_calibration','grade_velocity']:
            df[col] = df[col].map(_gpa)
        st.line_chart(df[['grade_breadth','grade_depth','grade_activation',
                           'grade_calibration','grade_velocity']], height=220)

# ------------------------------------------------------------------ #
# Tab 2 — Syllabus                                                     #
# ------------------------------------------------------------------ #
with tab_syllabus:
    st.subheader("Curriculum milestones")
    current_stage = t._current_stage()

    for idx, (stage_i, target) in enumerate(STAGE_TARGETS.items()):
        node_pct  = min(g.node_count / target['nodes'], 1.0) if stage_i == current_stage else (
            1.0 if stage_i < current_stage else 0.0)
        done      = stage_i < current_stage
        current   = stage_i == current_stage

        icon = "✅" if done else ("📖" if current else "🔒")
        colour = "#4a9eff" if current else ("#44ff88" if done else "#555")

        st.markdown(
            f"<div style='padding:10px 16px; margin:4px 0; border-radius:8px; "
            f"border:1px solid {colour}44; background:{colour}11;'>"
            f"<b style='color:{colour};'>{icon} {target['label']}</b>"
            f"{'  <span style=\"background:#4a9eff22; padding:2px 8px; border-radius:4px; font-size:0.8em;\">CURRENT</span>' if current else ''}"
            f"<br><span style='color:#aaa; font-size:0.85em;'>"
            f"Target: {target['nodes']} concepts · {target['edges']} connections"
            f"{'  —  You have: ' + str(g.node_count) + ' / ' + str(g.edge_count) if current else ''}"
            f"</span></div>",
            unsafe_allow_html=True,
        )
        if current:
            st.progress(min(g.node_count / target['nodes'], 1.0),
                        text=f"{g.node_count}/{target['nodes']} concepts")

# ------------------------------------------------------------------ #
# Tab 3 — Homework board                                               #
# ------------------------------------------------------------------ #
with tab_homework:
    hw = t.homework
    if not hw:
        st.info("No homework yet — run an assessment to generate assignments.")
    else:
        done_hw    = [h for h in hw if h['status'] == 'done']
        pending_hw = [h for h in hw if h['status'] == 'pending']

        p1, p2 = st.columns(2)
        p1.metric("Pending", len(pending_hw))
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

# ------------------------------------------------------------------ #
# Tab 4 — Full history                                                 #
# ------------------------------------------------------------------ #
with tab_history:
    st.subheader("All report cards")
    if cards:
        rows = []
        for c in reversed(cards):
            rows.append({
                'date':        c['timestamp'][:10],
                'time':        c['timestamp'][11:16] + ' UTC',
                'overall':     c['overall_grade'],
                'GPA':         c['gpa'],
                'breadth':     c['grade_breadth'],
                'depth':       c['grade_depth'],
                'activation':  c['grade_activation'],
                'calibration': c['grade_calibration'],
                'velocity':    c['grade_velocity'],
                'nodes':       c['node_count'],
                'edges':       c['edge_count'],
                'stage':       c['stage_label'],
                'comment':     c['narrative'],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
