"""
UI Page [10] — Cognition
========================
V3 live dashboard: all 8 new cognitive nodes in one place.

Tabs:
    🌡 Live          cognitive mode · goal · entropy · pathology flags
    🧩 Memory        3-layer temporal memory (working / episodic / semantic)
    🔗 Episodes      strongest transition paths · recent episode timeline
    ⚡ Predictions   predict next concepts from current active context
    💬 Contradictions open / resolved contradiction registry
    🔷 Abstractions   abstract concept nodes with stability / emergence
    🔮 Simulate       enter seeds → run sandbox propagation → trajectory
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import json
import streamlit as st
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

# ── shared CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.cog-card {
    background: #0f1117;
    border: 1px solid #1e2130;
    border-radius: 10px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.8rem;
}
.cog-pill {
    display: inline-block;
    padding: 3px 11px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    margin: 2px;
}
.cog-flag {
    background: #ff444422;
    border: 1px solid #ff4444;
    color: #ff7777;
}
.cog-ok {
    background: #44ff8822;
    border: 1px solid #44ff88;
    color: #44ff88;
}
.cog-mode {
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: 0.04em;
}
.ep-row {
    display: flex; align-items: center; gap: 0.5rem;
    padding: 5px 0; border-bottom: 1px solid #1e2130;
    font-size: 0.82rem; color: #bbb;
}
.ep-row:last-child { border-bottom: none; }
.ep-concept {
    background: #1a1f2e; color: #7eb8ff;
    padding: 2px 8px; border-radius: 4px;
    font-size: 0.78rem; font-family: monospace;
}
.ep-arrow { color: #555; }
.abs-card {
    border: 1px solid #2a2a3e;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 6px;
    background: #0d0f1a;
}
.abs-name { font-size: 1rem; font-weight: 600; color: #a78bfa; }
.abs-meta { font-size: 0.75rem; color: #666; }
.sim-step {
    border-left: 3px solid #4a9eff;
    padding: 8px 14px;
    margin-bottom: 6px;
    background: #0a0e1a;
    border-radius: 0 6px 6px 0;
}
.sim-step-n { font-size: 0.75rem; color: #4a9eff; font-weight: 700; margin-bottom: 4px; }
.sim-new { color: #44ff88; font-size: 0.78rem; }
</style>
""", unsafe_allow_html=True)


st.title("🧠 Cognition")
st.caption("V3 deep-memory & reasoning layer — live view of all cognitive subsystems")

# ── load singletons (lazy, non-crashing) ─────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _reflection():
    from reflection import ReflectionMonitor
    return ReflectionMonitor.get()

@st.cache_resource(show_spinner=False)
def _memory():
    from memory import TemporalMemory
    return TemporalMemory.get()

@st.cache_resource(show_spinner=False)
def _episodes():
    from episodes import EpisodeStore
    return EpisodeStore.get()

@st.cache_resource(show_spinner=False)
def _predictor():
    from predictor import Predictor
    return Predictor.get()

@st.cache_resource(show_spinner=False)
def _contradictions():
    from contradiction import ContradictionRegistry
    return ContradictionRegistry.get()

@st.cache_resource(show_spinner=False)
def _abstractor():
    from abstractor import Abstractor
    return Abstractor.get()

@st.cache_resource(show_spinner=False)
def _goals():
    from goals import GoalEngine
    return GoalEngine.get()

@st.cache_resource(show_spinner=False)
def _stability():
    from stability import StabilityMonitor
    return StabilityMonitor.get()


# ── tab layout ────────────────────────────────────────────────────────────────
tab_live, tab_mem, tab_ep, tab_pred, tab_contra, tab_abs, tab_sim, tab_wv = st.tabs([
    "🌡 Live", "🧩 Memory", "🔗 Episodes",
    "⚡ Predictions", "💬 Contradictions", "🔷 Abstractions", "🔮 Simulate", "🪞 Worldview"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Live
# ══════════════════════════════════════════════════════════════════════════════
with tab_live:
    rf = _reflection()
    ge = _goals()
    sm = _stability()

    try:
        report = rf.report()
    except Exception as e:
        st.warning(f"Reflection not yet warmed up: {e}")
        report = {}

    mode    = report.get('current_mode', 'reflective')
    entropy = report.get('entropy', 0.0)
    goal    = report.get('goal', 'maintain_stability')
    flags   = report.get('flags', [])
    desc    = rf.describe() if report else "No data yet."

    # Mode + flags row
    _MODE_COLOUR = {
        'focused':      '#f59e0b',
        'exploratory':  '#3b82f6',
        'associative':  '#8b5cf6',
        'exploitative': '#ef4444',
        'reflective':   '#10b981',
        'unknown':      '#6b7280',
    }
    mc = _MODE_COLOUR.get(mode, '#888')

    col_mode, col_goal = st.columns(2)
    with col_mode:
        st.markdown(f"""
        <div class="cog-card">
          <div style="font-size:0.75rem;color:#888;margin-bottom:6px">COGNITIVE MODE</div>
          <div class="cog-mode" style="color:{mc}">{'◉ ' + mode.upper()}</div>
          <div style="font-size:0.8rem;color:#999;margin-top:6px">entropy {entropy:.3f}</div>
        </div>
        """, unsafe_allow_html=True)
    with col_goal:
        st.markdown(f"""
        <div class="cog-card">
          <div style="font-size:0.75rem;color:#888;margin-bottom:6px">CURRENT DRIVE</div>
          <div class="cog-mode" style="color:#a78bfa">{goal.replace('_', ' ').upper()}</div>
          <div style="font-size:0.8rem;color:#999;margin-top:6px">argmax intrinsic drives</div>
        </div>
        """, unsafe_allow_html=True)

    # Entropy gauge
    st.subheader("Entropy", divider=False)
    st.progress(float(entropy), text=f"Shannon entropy: {entropy:.3f}  (healthy 0.20–0.80)")
    ec1, ec2, ec3 = st.columns(3)
    ec1.metric("Active concepts",  report.get('active_concept_count', 0))
    ec2.metric("Ambiguity load",   f"{report.get('ambiguity_load', 0.0):.2f}")
    ec3.metric("Novelty balance",  f"{report.get('novelty_balance', 0.0):.2f}")

    # Drive scores
    st.subheader("Drive scores", divider=False)
    scores = ge.drive_scores()
    if scores:
        df_scores = pd.DataFrame(
            [(k.replace('_', ' '), round(v, 4)) for k, v in scores.items()],
            columns=["Drive", "Score"]
        ).sort_values("Score", ascending=False)
        st.dataframe(df_scores, width='stretch', hide_index=True)

    # Pathology flags
    st.subheader("State flags", divider=False)
    if flags:
        flag_html = ' '.join(f'<span class="cog-pill cog-flag">{f}</span>' for f in flags)
        st.markdown(flag_html, unsafe_allow_html=True)
    else:
        st.markdown('<span class="cog-pill cog-ok">✓ healthy</span>', unsafe_allow_html=True)

    # Describe line
    st.markdown(f"<div style='color:#888;font-size:0.85rem;margin-top:12px'>{desc}</div>",
                unsafe_allow_html=True)

    # Region info
    region = report.get('dominant_region')
    r_count = report.get('region_count', 0)
    if region or r_count:
        st.caption(f"Active region: {region or 'none'} · {r_count} total regions")

    if st.button("🔄 Refresh", key="live_refresh"):
        st.cache_resource.clear()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Memory
# ══════════════════════════════════════════════════════════════════════════════
with tab_mem:
    mem = _memory()
    snap = mem.snapshot()

    w_count = snap.get('working_count', 0)
    e_count = snap.get('episodic_count', 0)
    s_count = snap.get('semantic_count', 0)

    st.subheader("Layer counts", divider=False)
    c1, c2, c3 = st.columns(3)
    c1.metric("Working", w_count,  help="Fast decay ~5min half-life")
    c2.metric("Episodic", e_count, help="Medium decay ~4h half-life")
    c3.metric("Semantic", s_count, help="Slow decay ~7-day half-life")

    # Layer fill bars
    max_count = max(w_count, e_count, s_count, 1)
    st.markdown("**Layer fill**")
    st.progress(w_count / max_count, text=f"Working  {w_count}")
    st.progress(e_count / max_count, text=f"Episodic {e_count}")
    st.progress(s_count / max_count, text=f"Semantic {s_count}")

    col_w, col_s = st.columns(2)
    with col_w:
        st.subheader("Top working (recent)", divider=False)
        top_w = snap.get('top_working', [])
        if top_w:
            df_w = pd.DataFrame(top_w, columns=['concept', 'value'])
            df_w['value'] = df_w['value'].round(4)
            st.dataframe(df_w, width='stretch', hide_index=True)
        else:
            st.caption("No working memory yet.")

    with col_s:
        st.subheader("Top semantic (deep knowledge)", divider=False)
        top_s = snap.get('top_semantic', [])
        if top_s:
            df_s = pd.DataFrame(top_s, columns=['concept', 'value'])
            df_s['value'] = df_s['value'].round(4)
            st.dataframe(df_s, width='stretch', hide_index=True)
        else:
            st.caption("Semantic memory empty — concepts consolidate after 3+ episodic hits.")

    st.caption("Decay: working 0.97/min · episodic 0.9997/min · semantic 0.99997/min")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Episodes
# ══════════════════════════════════════════════════════════════════════════════
with tab_ep:
    ep = _episodes()
    ep_snap = ep.snapshot()

    c1, c2 = st.columns(2)
    c1.metric("Transition edges", ep_snap.get('total_transitions', 0))
    c2.metric("Total episodes",   ep_snap.get('recent_episodes', 0))

    st.subheader("Strongest transition paths", divider=False)
    paths = ep.strongest_paths(n=30)
    if paths:
        rows_html = ''
        for a, b, w in paths[:20]:
            rows_html += f'''
            <div class="ep-row">
              <span class="ep-concept">{a[:30]}</span>
              <span class="ep-arrow">→</span>
              <span class="ep-concept">{b[:30]}</span>
              <span style="margin-left:auto;color:#888;font-size:0.78rem">{w:.2f}</span>
            </div>'''
        st.markdown(f'<div class="cog-card">{rows_html}</div>', unsafe_allow_html=True)
    else:
        st.info("No transitions yet — accept lessons to build the episode graph.")

    st.subheader("Recent episodes", divider=False)
    recent = ep.recent(n=10)
    if recent:
        for ep_item in reversed(recent):
            concepts_str = " → ".join(ep_item.get('concepts', [])[:6])
            amb   = ep_item.get('ambiguity', 0.0)
            ts    = ep_item.get('ts', '')[:16]
            region = ep_item.get('active_region') or ''
            with st.expander(f"{ts}  |  {len(ep_item.get('concepts',[]))} concepts  |  amb {amb:.2f}"):
                st.markdown(f"<div style='font-size:0.82rem;color:#bbb'>{concepts_str}</div>",
                            unsafe_allow_html=True)
                if region:
                    st.caption(f"Region: {region}")
    else:
        st.info("No episodes recorded yet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Predictions
# ══════════════════════════════════════════════════════════════════════════════
with tab_pred:
    pred = _predictor()
    mem4 = _memory()

    st.subheader("Predict from active working memory", divider=False)
    top_working = [c for c, _ in mem4.top_working(10)]

    if top_working:
        st.caption(f"Context: {', '.join(top_working[:5])}")
        preds = pred.predict(top_working[:5])
        if preds:
            df_p = pd.DataFrame(preds, columns=['concept', 'probability'])
            df_p['probability'] = df_p['probability'].round(4)
            st.dataframe(df_p, width='stretch', hide_index=True)
            st.caption("These concepts are pre-activated before the next lesson arrives.")
        else:
            st.info("No transitions from current context yet. Accept more lessons.")
    else:
        st.info("Working memory empty. Accept lessons to populate context.")

    st.divider()
    st.subheader("Manual prediction", divider=False)
    user_ctx = st.text_input("Enter concept(s) (comma-separated)", placeholder="wave, frequency, rhythm")
    if user_ctx.strip():
        seeds = [c.strip() for c in user_ctx.split(',') if c.strip()]
        manual_preds = pred.predict(seeds)
        if manual_preds:
            df_mp = pd.DataFrame(manual_preds, columns=['predicted concept', 'probability'])
            df_mp['probability'] = df_mp['probability'].round(4)
            st.dataframe(df_mp, width='stretch', hide_index=True)
        else:
            st.info("No transitions found for those concepts yet.")

    # Last predictions
    last = pred.last_predictions()
    if last:
        st.caption(f"Last auto-prediction: {[c for c,_ in last[:3]]}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Contradictions
# ══════════════════════════════════════════════════════════════════════════════
with tab_contra:
    cr = _contradictions()
    c_snap = cr.snapshot()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total",    c_snap.get('total', 0))
    c2.metric("Open",     c_snap.get('open', 0),     delta=None)
    c3.metric("Resolved", c_snap.get('resolved', 0))

    st.subheader("Open contradictions", divider=False)
    unres = cr.unresolved()
    if unres:
        for item in unres:
            with st.container():
                col_a, col_arr, col_b, col_act = st.columns([3, 1, 3, 2])
                col_a.markdown(f"**{item['concept_a'][:40]}**")
                col_arr.markdown("↔")
                col_b.markdown(f"**{item['concept_b'][:40]}**")
                if col_act.button("Resolve", key=f"res_{item['id']}"):
                    cr.resolve(item['id'])
                    st.rerun()
                st.caption(
                    f"Type: {item.get('conflict_type')} · "
                    f"tension A {item.get('tension_a', 0):.2f} / B {item.get('tension_b', 0):.2f} · "
                    f"first seen {item.get('first_seen', '')[:10]}"
                )
                st.divider()
    else:
        st.success("No open contradictions — system is internally consistent.")
        st.caption("Contradictions are detected when two hot concepts appear in bidirectional transitions.")

    with st.expander("All contradictions (including resolved)"):
        all_c = cr.all()
        if all_c:
            df_c = pd.DataFrame([{
                'A': c['concept_a'][:30],
                'B': c['concept_b'][:30],
                'type': c['conflict_type'],
                'status': c['resolution_status'],
                'tension_a': c.get('tension_a', 0),
                'tension_b': c.get('tension_b', 0),
            } for c in all_c])
            st.dataframe(df_c, width='stretch', hide_index=True)
        else:
            st.caption("None yet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Abstractions
# ══════════════════════════════════════════════════════════════════════════════
with tab_abs:
    ab = _abstractor()
    ab_snap = ab.snapshot()

    c1, c2 = st.columns(2)
    c1.metric("Abstract concepts", ab_snap.get('total', 0))
    c2.metric("Stable (≥0.7)",     ab_snap.get('stable', 0))

    if st.button("🔄 Run abstractor now", key="run_abs"):
        with st.spinner("Detecting clusters…"):
            ab.run()
        st.rerun()

    all_abs = ab.all()
    if all_abs:
        sorted_abs = sorted(all_abs,
                            key=lambda a: a.get('emergence_score', 0),
                            reverse=True)
        for a in sorted_abs:
            stab  = a.get('stability', 0.0)
            stab_colour = '#44ff88' if stab >= 0.7 else ('#f59e0b' if stab >= 0.4 else '#888')
            members = ', '.join(a.get('members', [])[:8])
            if len(a.get('members', [])) > 8:
                members += ' …'
            st.markdown(f"""
            <div class="abs-card">
              <div class="abs-name">{a.get('name', '~?')}</div>
              <div class="abs-meta">
                emergence {a.get('emergence_score', 0):.3f} &nbsp;·&nbsp;
                <span style="color:{stab_colour}">stability {stab:.2f}</span> &nbsp;·&nbsp;
                seen {a.get('reuse_frequency', 0)}× &nbsp;·&nbsp;
                {len(a.get('members',[]))} members
              </div>
              <div style="font-size:0.8rem;color:#666;margin-top:4px">{members}</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info(
            "No abstractions yet. Abstractions emerge after:\n"
            "- 5+ co-occurrences between concepts\n"
            "- Cluster of ≥3 members\n"
            "- Run at every 3h check-in automatically"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — Simulate
# ══════════════════════════════════════════════════════════════════════════════
with tab_sim:
    from simulator import Simulator
    sim = Simulator()

    st.subheader("Sandbox cognition", divider=False)
    st.caption("Runs hypothetical activation propagation — no real state is changed.")

    mem7 = _memory()
    default_seeds = ', '.join(c for c, _ in mem7.top_working(5))

    seeds_input = st.text_input(
        "Seed concepts (comma-separated)",
        value=default_seeds,
        placeholder="Enter concepts…"
    )
    n_steps = st.slider("Propagation steps", min_value=1, max_value=5, value=3)

    if st.button("▶ Run simulation", type="primary", key="run_sim"):
        seeds = [c.strip() for c in seeds_input.split(',') if c.strip()]
        if not seeds:
            st.warning("Enter at least one seed concept.")
        else:
            with st.spinner("Simulating…"):
                result = sim.simulate(seeds, steps=n_steps)

            c1, c2 = st.columns(2)
            c1.metric("Concepts touched", result.get('total_concepts_touched', 0))
            c2.metric("Terminal concepts", len(result.get('terminal_concepts', [])))

            st.subheader("Trajectory", divider=False)
            for step_data in result.get('steps', []):
                n     = step_data['step']
                top   = step_data.get('concepts', [])[:6]
                new_a = step_data.get('new_arrivals', [])
                pred_ = step_data.get('predicted', [])

                top_str  = ' · '.join(top)
                new_str  = ' · '.join(new_a) if new_a else '—'
                pred_str = ' · '.join(pred_) if pred_ else '—'

                st.markdown(f"""
                <div class="sim-step">
                  <div class="sim-step-n">STEP {n}</div>
                  <div style="font-size:0.82rem;color:#bbb">Active: {top_str}</div>
                  <div class="sim-new">New arrivals: {new_str}</div>
                  <div style="font-size:0.75rem;color:#555">Predicted from: {pred_str}</div>
                </div>
                """, unsafe_allow_html=True)

            st.subheader("Terminal state", divider=False)
            terminal = result.get('terminal_concepts', [])
            if terminal:
                st.markdown(
                    ' '.join(
                        f'<span class="cog-pill" style="background:#1a2a1a;border:1px solid #2a4a2a;color:#44ff88">{c}</span>'
                        for c in terminal[:12]
                    ),
                    unsafe_allow_html=True
                )
            st.caption("This is where the system's attention would naturally settle after the simulated propagation.")

    else:
        st.info("Simulation runs in a read-only scratchpad — no concepts are stored, no memory is changed.")


# ── 🪞 Worldview ──────────────────────────────────────────────────────────────
with tab_wv:
    st.caption("What has survived. The engine's accumulated cognitive identity.")

    @st.cache_resource(show_spinner=False)
    def _worldview():
        from worldview import Worldview
        return Worldview.get()

    wv = _worldview()
    snap = wv.snapshot()

    # Summary banner
    summary = snap.get('identity_summary', '')
    if summary:
        st.markdown(f"""
        <div class="cog-card" style="border-color:#4a9eff;margin-bottom:1.2rem">
          <div style="font-size:0.72rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Identity Summary</div>
          <div style="color:#ccc;font-size:0.92rem;line-height:1.6">{summary}</div>
        </div>""", unsafe_allow_html=True)

    col_upd, col_btn = st.columns([3, 1])
    col_upd.caption(f"Last updated: {snap.get('last_updated', 'never')}  ·  Update #{snap.get('update_count', 0)}")
    if col_btn.button("Refresh worldview", width='stretch'):
        wv.update()
        st.rerun()

    st.divider()

    # ── Persistent concepts ────────────────────────────────────────────────
    st.subheader("Persistent Concepts")
    st.caption("Durably encoded in semantic memory — what the engine has truly learned.")
    pc = snap.get('persistent_concepts', [])
    if pc:
        df_pc = pd.DataFrame(pc).rename(columns={'concept': 'Concept', 'semantic_value': 'Semantic Value'})
        st.dataframe(df_pc.head(30), width='stretch', hide_index=True)
    else:
        st.info("No persistent concepts yet — semantic memory needs more time.")

    st.divider()

    # ── Chronic contradictions ─────────────────────────────────────────────
    st.subheader("Chronic Contradictions")
    st.caption("Open tensions older than 24 hours — structural questions the engine lives with.")
    cc = snap.get('chronic_contradictions', [])
    if cc:
        for c in cc:
            st.markdown(f"""
            <div class="cog-card" style="border-color:#ff4444">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                  <span class="ep-concept">{c['concept_a']}</span>
                  <span class="ep-arrow" style="margin:0 8px">⟷</span>
                  <span class="ep-concept">{c['concept_b']}</span>
                </div>
                <div style="font-family:monospace;font-size:0.75rem;color:#777">{c['age_hours']:.0f}h open</div>
              </div>
              <div style="margin-top:6px;font-size:0.78rem;color:#888">
                tension A={c['tension_a']} · tension B={c['tension_b']} · type: {c['conflict_type']}
              </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("No chronic contradictions — all tensions are recent or resolved.")

    st.divider()

    # ── Surviving goals ────────────────────────────────────────────────────
    st.subheader("Surviving Goals")
    st.caption("Goals that fire across multiple cognitive modes — stable intrinsic drives.")
    sg = snap.get('surviving_goals', [])
    if sg:
        for g_item in sg:
            modes_str = ' · '.join(g_item['distinct_modes'])
            bar_w = int(g_item['stability_score'] * 100)
            st.markdown(f"""
            <div class="cog-card">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <div style="color:#a78bfa;font-weight:600">{g_item['goal'].replace('_',' ')}</div>
                <div style="font-family:monospace;font-size:0.75rem;color:#777">{g_item['recurrence']}× fired</div>
              </div>
              <div style="font-size:0.78rem;color:#888;margin-bottom:8px">modes: {modes_str}</div>
              <div style="height:3px;background:#1e2130;border-radius:2px">
                <div style="height:100%;width:{bar_w}%;background:#a78bfa;border-radius:2px"></div>
              </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("Goals haven't survived across modes yet — more training needed.")

    st.divider()

    # ── Home regions ───────────────────────────────────────────────────────
    st.subheader("Home Territories")
    st.caption("Regions the engine returns to most — its knowledge domains.")
    hr = snap.get('home_regions', [])
    if hr:
        cols = st.columns(min(len(hr), 4))
        for i, r in enumerate(hr[:4]):
            with cols[i]:
                st.metric(r['region_id'], f"{r['visit_count']} visits", f"{r['dominance_pct']}% of all visits")
    else:
        st.info("No home territories yet — need more region-tagged episodes.")

    st.divider()

    # ── Foundational abstractions ──────────────────────────────────────────
    st.subheader("Foundational Abstractions")
    st.caption("High-stability abstract concepts that anchor the engine's reasoning.")
    fa = snap.get('foundational_abstractions', [])
    if fa:
        df_fa = pd.DataFrame(fa).rename(columns={
            'name': 'Abstract Concept', 'stability': 'Stability',
            'reuse_frequency': 'Reuse', 'member_count': 'Members',
            'emergence_score': 'Emergence',
        })
        st.dataframe(df_fa, width='stretch', hide_index=True)
    else:
        st.info("No foundational abstractions yet — abstractions need more reuse cycles.")

    # ── Semantic biases ────────────────────────────────────────────────────
    sb = snap.get('semantic_biases', {})
    if sb:
        st.divider()
        st.subheader("Semantic Biases")
        st.caption("Fraction of deep memory devoted to each region — where knowledge has accumulated.")
        df_sb = pd.DataFrame([
            {'Region': k, 'Semantic weight': v} for k, v in sb.items()
        ])
        st.dataframe(df_sb, width='stretch', hide_index=True)
