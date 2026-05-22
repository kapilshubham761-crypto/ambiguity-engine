"""7.6 — A/B page: modulated vs control outputs side-by-side with deltas."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import json
import streamlit as st
import pandas as pd

st.title("🔀 A / B")
st.caption("Modulated vs control outputs side-by-side. Use the Runner page to generate entries.")

log_path = os.path.join(ROOT, 'logs', 'ab_log.jsonl')

if not os.path.exists(log_path):
    st.info("No A/B log yet — click 'Run A/B' on the Runner page.")
    st.stop()

entries = [json.loads(l) for l in open(log_path, encoding='utf-8') if l.strip()]

if not entries:
    st.info("Log exists but is empty.")
    st.stop()

# ------------------------------------------------------------------ #
# Summary table                                                        #
# ------------------------------------------------------------------ #
st.subheader(f"{len(entries)} logged comparisons")

summary = pd.DataFrame([
    {
        'time':     e['timestamp'][:16].replace('T', ' '),
        'level':    e['ambiguity_level'],
        'score':    round(e['ambiguity_score'], 3),
        'concepts': ', '.join(e.get('concepts', [])),
        'input':    e['input'][:60] + ('…' if len(e['input']) > 60 else ''),
    }
    for e in entries
])
st.dataframe(summary, use_container_width=True, hide_index=True)

st.divider()

# ------------------------------------------------------------------ #
# Detail view for one entry                                            #
# ------------------------------------------------------------------ #
st.subheader("Detail view")
idx = st.selectbox(
    "Select entry",
    range(len(entries)),
    format_func=lambda i: f"[{entries[i]['timestamp'][:16]}]  {entries[i]['input'][:55]}",
)

e = entries[idx]

st.markdown(f"**Input:** {e['input']}")
st.markdown(
    f"**Ambiguity:** `{e['ambiguity_score']:.3f}` &nbsp; `{e['ambiguity_level'].upper()}`  "
    f"&nbsp; concepts: " + ", ".join(f"`{c}`" for c in e.get('concepts', []))
)
if e.get('neighbours'):
    st.markdown("**Neighbours injected:** " + ", ".join(f"`{n}`" for n in e['neighbours']))

with st.expander("System prompt used"):
    st.code(e.get('system_prompt', ''), language=None)

st.divider()

col_mod, col_ctrl = st.columns(2)

with col_mod:
    st.markdown(f"#### Modulated &nbsp; `{e['ambiguity_level'].upper()}`")
    st.write(e.get('modulated_output', ''))

with col_ctrl:
    st.markdown("#### Control &nbsp; `LOW`")
    st.write(e.get('control_output', ''))

# ------------------------------------------------------------------ #
# Word-count delta                                                     #
# ------------------------------------------------------------------ #
mod_words  = len(e.get('modulated_output', '').split())
ctrl_words = len(e.get('control_output', '').split())
delta      = mod_words - ctrl_words
sign       = '+' if delta >= 0 else ''

st.divider()
d1, d2, d3 = st.columns(3)
d1.metric("Modulated words",  mod_words)
d2.metric("Control words",    ctrl_words)
d3.metric("Delta",            f"{sign}{delta}")
