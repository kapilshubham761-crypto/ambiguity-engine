"""A/B — modulated vs control: run suite, blind judge, results."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json, random, yaml
import streamlit as st
import pandas as pd

st.title("🔀 A / B Evaluation")

CFG      = yaml.safe_load(open(os.path.join(ROOT, 'config.yaml')))
LOG_PATH = os.path.join(ROOT, 'logs', 'ab_log.jsonl')
JDG_PATH = os.path.join(ROOT, 'logs', 'ab_judgments.jsonl')
os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)

def _load_log():
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    for l in open(LOG_PATH, encoding='utf-8'):
        try:
            if l.strip():
                out.append(json.loads(l))
        except Exception:
            pass
    return out

def _load_judgments():
    if not os.path.exists(JDG_PATH):
        return []
    out = []
    for l in open(JDG_PATH, encoding='utf-8'):
        try:
            if l.strip():
                out.append(json.loads(l))
        except Exception:
            pass
    return out

tab_suite, tab_judge, tab_results, tab_log = st.tabs([
    "🧪 Run Suite", "🙈 Blind Judge", "📊 Results", "📋 Log"
])

# ================================================================== #
# TAB 1 — Run Suite                                                   #
# ================================================================== #
with tab_suite:
    from eval_prompts import EVAL_PROMPTS

    st.markdown(
        "Run the 30 deliberate test prompts through the full pipeline. "
        "Each generates an A/B log entry for blind judging."
    )

    already_run = {e['input'] for e in _load_log()}
    remaining   = [p for p in EVAL_PROMPTS if p['prompt'] not in already_run]

    c1, c2, c3 = st.columns(3)
    c1.metric("Total prompts",  len(EVAL_PROMPTS))
    c2.metric("Already run",    len(EVAL_PROMPTS) - len(remaining))
    c3.metric("Remaining",      len(remaining))

    st.divider()

    if not remaining:
        st.success("All 30 prompts have been run. Head to **Blind Judge** to evaluate.")
        st.divider()
        col_reset, _ = st.columns([1, 2])
        with col_reset:
            if st.button("🔄 Reset — re-run all 30 prompts", use_container_width=True):
                if os.path.exists(LOG_PATH):
                    os.remove(LOG_PATH)
                st.rerun()
    else:
        # Show next prompt
        next_p = remaining[0]
        st.markdown(f"**Next prompt** &nbsp; `{next_p['id']}` &nbsp; expected: `{next_p['expected'].upper()}`")
        st.info(f"\"{next_p['prompt']}\"")

        run_next = st.button("▶ Run next prompt", type="primary", use_container_width=True)
        run_all  = st.button(
            f"⚡ Run all {len(remaining)} remaining",
            use_container_width=True,
            disabled=len(remaining) > 15,
            help="Only enabled when ≤15 remain to avoid long waits"
        )

        if run_next or run_all:
            to_run = remaining if run_all else [next_p]

            from graph import SemanticGraph
            from extractor import extract
            from detector import detect_and_log
            from modulator import build_prompt, run_ab

            @st.cache_resource(show_spinner=False)
            def _graph():
                return SemanticGraph()
            g = _graph()

            bar = st.progress(0, text="Starting…")
            for i, p in enumerate(to_run):
                bar.progress((i) / len(to_run), text=f"Running {p['id']}: {p['prompt'][:50]}…")
                try:
                    concepts = extract(p['prompt'])
                    result   = detect_and_log(p['prompt'], concepts, graph=g)
                    run_ab(p['prompt'], concepts, result, g, CFG)
                except Exception as ex:
                    st.warning(f"{p['id']} failed: {ex}")

            bar.progress(1.0, text="Done!")
            st.success(f"Ran {len(to_run)} prompt(s). Refresh to see updated count.")
            st.rerun()

# ================================================================== #
# TAB 2 — Blind Judge                                                 #
# ================================================================== #
with tab_judge:
    entries   = _load_log()
    judgments = _load_judgments()
    judged_inputs = {j['input'] for j in judgments}

    unjudged = [e for e in entries if e['input'] not in judged_inputs]

    st.markdown(
        "Each pair is shown **without labels** — you don't know which is "
        "modulated and which is control. Pick the better response, or call it a tie."
    )

    jc1, jc2, jc3 = st.columns(3)
    jc1.metric("Total pairs",   len(entries))
    jc2.metric("Judged",        len(judgments))
    jc3.metric("Remaining",     len(unjudged))

    if not unjudged:
        st.success("All pairs judged! See Results tab.")
    else:
        st.divider()

        # Pick a random unjudged entry for this session
        if 'judge_entry_idx' not in st.session_state or \
           st.session_state.get('judge_entry_input') not in {e['input'] for e in unjudged}:
            e = random.choice(unjudged)
            # Randomly flip which side is A/B so position bias is controlled
            flipped = random.random() > 0.5
            st.session_state['judge_entry_input'] = e['input']
            st.session_state['judge_flipped']     = flipped

        e       = next(x for x in unjudged if x['input'] == st.session_state['judge_entry_input'])
        flipped = st.session_state['judge_flipped']

        output_a = e['control_output']    if flipped else e['modulated_output']
        output_b = e['modulated_output']  if flipped else e['control_output']

        st.markdown(f"**Prompt:** {e['input']}")
        st.caption(f"Ambiguity detected: `{e['ambiguity_score']:.3f}` `{e['ambiguity_level'].upper()}`"
                   f" · concepts: {', '.join(e.get('concepts', []))}")

        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### Response A")
            st.write(output_a)
        with col_b:
            st.markdown("#### Response B")
            st.write(output_b)

        st.divider()
        st.markdown("**Which response is more thoughtful, careful, or aware of nuance?**")
        j1, j2, j3, j4 = st.columns(4)

        def _save_judgment(choice: str):
            # Decode which was actually modulated
            if flipped:
                modulated_won = (choice == 'A')
            else:
                modulated_won = (choice == 'B')

            entry = {
                'input':           e['input'],
                'ambiguity_score': e['ambiguity_score'],
                'ambiguity_level': e['ambiguity_level'],
                'choice':          choice,
                'modulated_won':   modulated_won if choice != 'tie' else None,
                'flipped':         flipped,
            }
            with open(JDG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
            # Force new entry next render
            del st.session_state['judge_entry_input']

        if j1.button("👈 A is better",  use_container_width=True, type="primary"):
            _save_judgment('A')
            st.rerun()
        if j2.button("👉 B is better",  use_container_width=True, type="primary"):
            _save_judgment('B')
            st.rerun()
        if j3.button("🤝 Same / tie",   use_container_width=True):
            _save_judgment('tie')
            st.rerun()
        if j4.button("⏭ Skip",          use_container_width=True):
            del st.session_state['judge_entry_input']
            st.rerun()

# ================================================================== #
# TAB 3 — Results                                                     #
# ================================================================== #
with tab_results:
    judgments = _load_judgments()

    if len(judgments) < 3:
        st.info("Need at least 3 judgments to show results. Go to Blind Judge.")

    decided  = [j for j in judgments if j['choice'] != 'tie']
    ties     = [j for j in judgments if j['choice'] == 'tie']
    mod_wins = [j for j in decided if j['modulated_won']]

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Total judged",       len(judgments))
    r2.metric("Modulated won",      len(mod_wins))
    r3.metric("Control won",        len(decided) - len(mod_wins))
    r4.metric("Ties",               len(ties))

    if decided:
        rate = len(mod_wins) / len(decided)
        colour = "green" if rate > 0.6 else "orange" if rate > 0.45 else "red"
        st.markdown(
            f"**Modulated win rate (excl. ties): "
            f"<span style='color:{colour}'>{rate:.0%}</span>**",
            unsafe_allow_html=True,
        )
        if rate > 0.65:
            st.success("Strong signal — modulation is producing meaningfully better outputs.")
        elif rate > 0.55:
            st.info("Weak signal — modulation may be helping, but not clearly. Run more pairs.")
        elif rate > 0.45:
            st.warning("No signal — modulation is not reliably better than control.")
        else:
            st.error("Negative signal — control is outperforming modulated outputs.")

    st.divider()

    # Break down by ambiguity level
    st.markdown("**Win rate by ambiguity level**")
    for level in ['low', 'medium', 'high']:
        level_decided = [j for j in decided if j['ambiguity_level'] == level]
        level_wins    = [j for j in level_decided if j['modulated_won']]
        if level_decided:
            r = len(level_wins) / len(level_decided)
            st.markdown(
                f"- `{level.upper()}`: {len(level_wins)}/{len(level_decided)} = **{r:.0%}**"
            )
        else:
            st.markdown(f"- `{level.upper()}`: no data yet")

    st.divider()

    # Score breakdown
    if len(judgments) >= 5:
        st.markdown("**Score distribution of judged pairs**")
        df = pd.DataFrame([{
            'score': j['ambiguity_score'],
            'level': j['ambiguity_level'],
            'outcome': ('modulated' if j['modulated_won'] else 'control') if j['choice'] != 'tie' else 'tie'
        } for j in judgments])
        st.dataframe(df, use_container_width=True, hide_index=True)

# ================================================================== #
# TAB 4 — Raw log                                                     #
# ================================================================== #
with tab_log:
    entries = _load_log()
    if not entries:
        st.info("No A/B log yet — run prompts in the Suite tab.")

    summary = pd.DataFrame([{
        'id':    e['timestamp'][:16].replace('T', ' '),
        'level': e['ambiguity_level'],
        'score': round(e['ambiguity_score'], 3),
        'input': e['input'][:65] + ('…' if len(e['input']) > 65 else ''),
    } for e in entries])
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.divider()
    idx = st.selectbox(
        "Inspect entry",
        range(len(entries)),
        format_func=lambda i: f"[{entries[i]['timestamp'][:16]}]  {entries[i]['input'][:55]}",
    )
    e = entries[idx]
    st.markdown(f"**Score:** `{e['ambiguity_score']:.3f}` `{e['ambiguity_level'].upper()}`")
    st.markdown("**Neighbours:** " + (', '.join(f"`{n}`" for n in e.get('neighbours', [])) or '—'))
    with st.expander("System prompt"):
        st.code(e.get('system_prompt', ''), language=None)

    c_mod, c_ctrl = st.columns(2)
    with c_mod:
        st.markdown(f"#### Modulated `{e['ambiguity_level'].upper()}`")
        st.write(e.get('modulated_output', ''))
    with c_ctrl:
        st.markdown("#### Control `LOW`")
        st.write(e.get('control_output', ''))
