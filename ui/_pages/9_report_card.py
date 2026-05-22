"""
Report Card — cognitive development tracker.

Sections:
    Birth certificate   age in days, total concepts & connections
    Current grade       6 metrics (breadth, depth, activation, calibration, velocity, overall)
    Teacher's comment   one-line narrative
Tabs:
    📈 Progress         node/edge growth + GPA chart
    📚 Syllabus         curriculum milestones with progress bars
    📝 Homework         pending & completed topic assignments
    🗂  History          full card ledger table
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import sys, os as _os
sys.path.insert(0, _os.join(_os.path.dirname(__file__), '..', 'components'))

import json
from datetime import datetime, date, timezone
import streamlit as st
import pandas as pd

from graph import SemanticGraph
from assessor import load_cards, assess, save_card, BIRTHDATE, STAGE_TARGETS
from teacher import Teacher
from grade_card import render_grade_row, render_syllabus, render_homework_board

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
    from meta_state import MetaState
    t = Teacher(meta=MetaState.get())
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

with col_grade:
    render_grade_row(latest)

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
    render_syllabus(g, t)

# ------------------------------------------------------------------ #
# Tab 3 — Homework board                                               #
# ------------------------------------------------------------------ #
with tab_homework:
    render_homework_board(t)

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
