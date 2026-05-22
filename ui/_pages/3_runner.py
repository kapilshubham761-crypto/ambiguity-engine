"""
Runner — manual pipeline driver.

Sends a user prompt through the full pipeline in sequence:
    ① Extract concepts      (spaCy + MiniLM)
    ② Detect ambiguity      (variance + cluster + bridge metrics)
    ③ Build modulation      (select system prompt based on level)
    ④ Call LLM              (Ollama / Qwen)
    ⑤ Update graph          (save new concepts + edges)

Optional: Run A/B mode — fires modulated + control call side-by-side.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import yaml
import streamlit as st

from graph import SemanticGraph
from extractor import extract
from detector import detect_and_log
from modulator import build_prompt, call_llm

st.title("▶️ Runner")
st.caption("Send a prompt through the full pipeline and watch the engine think.")

CFG = yaml.safe_load(open(os.path.join(ROOT, 'config.yaml')))


# ======================================================================== #
# Graph load (cached singleton)                                             #
# ======================================================================== #

@st.cache_resource(show_spinner="Loading graph…")
def _load_graph():
    return SemanticGraph()

g = _load_graph()


# ======================================================================== #
# Input                                                                     #
# ======================================================================== #

user_input = st.text_area(
    "Prompt",
    placeholder="Type anything — a question, a fragment, a tension…",
    height=100,
)

run_col, ab_col = st.columns(2)
run_btn = run_col.button("Run",                           type="primary", use_container_width=True)
ab_btn  = ab_col.button("Run A/B (also logs to ab_log)",                 use_container_width=True)

if not (run_btn or ab_btn) or not user_input.strip():
    st.stop()


# ======================================================================== #
# Pipeline execution                                                        #
# ======================================================================== #

with st.spinner("Extracting concepts…"):
    concepts = extract(user_input)

with st.spinner("Detecting ambiguity…"):
    result = detect_and_log(user_input, concepts, graph=g)

with st.spinner("Building modulation prompt…"):
    mod = build_prompt(concepts, result, graph=g)

# Update graph and invalidate cache so State page sees the new data
node_ids = g.update(concepts)
g.save()
st.cache_resource.clear()

st.divider()


# ======================================================================== #
# ① Concepts extracted                                                      #
# ======================================================================== #

st.subheader("① Concepts extracted")
if concepts:
    cols = st.columns(min(len(concepts), 5))
    for i, c in enumerate(concepts):
        cols[i % len(cols)].markdown(f"`{c.text}`  \n*{c.source}*")
else:
    st.warning("No concepts extracted.")

st.divider()


# ======================================================================== #
# ② Ambiguity                                                               #
# ======================================================================== #

st.subheader("② Ambiguity")

LEVEL_COLOUR = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}
st.markdown(
    f"**Score: {result.score:.3f}** &nbsp; "
    f"{LEVEL_COLOUR.get(result.level, '')} `{result.level.upper()}`"
)

m1, m2, m3 = st.columns(3)
m1.metric("Variance", f"{result.variance:.3f}")
m2.metric("Cluster",  f"{result.cluster:.3f}")
m3.metric("Bridge",   f"{result.bridge:.3f}")

st.divider()


# ======================================================================== #
# ③ Modulation                                                              #
# ======================================================================== #

st.subheader("③ Modulation")

st.markdown(f"**Level:** `{mod.level}`")
if mod.neighbours:
    st.markdown("**Injected neighbours:** " + ", ".join(f"`{n}`" for n in mod.neighbours))
with st.expander("System prompt sent to LLM"):
    st.code(mod.system_prompt, language=None)

st.divider()


# ======================================================================== #
# ④ LLM output                                                              #
# ======================================================================== #

st.subheader("④ LLM output")

if ab_btn:
    from modulator import run_ab
    with st.spinner("Running A/B (two LLM calls)…"):
        entry = run_ab(user_input, concepts, result, g, CFG)

    c_mod, c_ctrl = st.columns(2)
    with c_mod:
        st.markdown(f"**Modulated** `{mod.level}`")
        st.write(entry['modulated_output'])
    with c_ctrl:
        st.markdown("**Control** `low`")
        st.write(entry['control_output'])
    st.success("A/B entry written to logs/ab_log.jsonl")
else:
    with st.spinner("Calling LLM…"):
        response = call_llm(user_input, mod.system_prompt, CFG)
    st.write(response)
