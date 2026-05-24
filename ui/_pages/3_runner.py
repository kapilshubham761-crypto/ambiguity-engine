"""
Runner — manual pipeline driver.

Sends a user prompt through the full pipeline:
    ① Extract concepts      (spaCy + MiniLM)
    ② Detect ambiguity      (variance + cluster + bridge metrics)
    ③ Build modulation      (select system prompt based on level)
    ④ Call LLM              (Ollama / Qwen)
    ⑤ Feed response back    (engine learns from its own answers)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
ROOT = os.path.join(os.path.dirname(__file__), '..', '..')

import yaml
import streamlit as st

from graph     import SemanticGraph
from extractor import extract
from detector  import detect_and_log
from modulator import build_prompt, call_llm, run_ab
from meta_state import MetaState

st.title("Runner")
st.caption("Send a prompt through the full pipeline and watch the engine think.")

# ── Config ────────────────────────────────────────────────────────────────────

CFG = yaml.safe_load(open(os.path.join(ROOT, 'config.yaml')))

with st.sidebar:
    st.markdown("**Model**")
    _models = ['qwen2.5:3b-instruct', 'qwen2.5:7b-instruct', 'llama3.2:3b']
    _chosen = st.selectbox("Ollama model", _models,
                           index=_models.index(CFG['model']['name'])
                           if CFG['model']['name'] in _models else 0,
                           label_visibility='collapsed')
    CFG['model']['name'] = _chosen

# ── Graph ─────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="Loading graph…")
def _load_graph():
    return SemanticGraph()

g = _load_graph()

# ── Input ─────────────────────────────────────────────────────────────────────

user_input = st.text_area(
    "Prompt",
    placeholder="Type anything — a question, a fragment, a tension…",
    height=100,
)

run_col, ab_col = st.columns(2)
run_btn = run_col.button("Run",                          type="primary", width='stretch')
ab_btn  = ab_col.button("Run A/B (logs to ab_log.jsonl)",               width='stretch')

if not (run_btn or ab_btn) or not user_input.strip():
    st.stop()

# ── Pipeline ──────────────────────────────────────────────────────────────────

with st.spinner("Extracting concepts…"):
    concepts = extract(user_input)

with st.spinner("Detecting ambiguity…"):
    result = detect_and_log(user_input, concepts, graph=g)

with st.spinner("Building modulation prompt…"):
    mod = build_prompt(concepts, result, graph=g, meta=MetaState.get())

g.update(concepts)
g.save()
st.cache_resource.clear()

st.divider()

# ── ① Concepts ────────────────────────────────────────────────────────────────

st.subheader("① Concepts extracted")
if concepts:
    cols = st.columns(min(len(concepts), 5))
    for i, c in enumerate(concepts):
        cols[i % len(cols)].markdown(f"`{c.text}`  \n*{c.source}*")
else:
    st.warning("No concepts extracted.")

st.divider()

# ── ② Ambiguity ───────────────────────────────────────────────────────────────

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

# ── ③ Modulation ──────────────────────────────────────────────────────────────

st.subheader("③ Modulation")
st.markdown(f"**Level:** `{mod.level}`  &nbsp; **Model:** `{CFG['model']['name']}`")
if mod.neighbours:
    st.markdown("**Graph neighbours injected:** " + ", ".join(f"`{n}`" for n in mod.neighbours))
if mod.meta_concepts:
    st.markdown("**Meta-state pressure:** " + ", ".join(f"`{c}`" for c in mod.meta_concepts))
with st.expander("System prompt sent to LLM"):
    st.code(mod.system_prompt, language=None)

st.divider()

# ── ④ LLM output ──────────────────────────────────────────────────────────────

st.subheader("④ LLM output")

response = ''

if ab_btn:
    with st.spinner(f"Running A/B on {CFG['model']['name']} (two calls)…"):
        entry = run_ab(user_input, concepts, result, g, CFG, meta=MetaState.get())
    response = entry['modulated_output']

    c_mod, c_ctrl = st.columns(2)
    with c_mod:
        st.markdown(f"**Modulated** `{mod.level}`")
        st.write(entry['modulated_output'])
    with c_ctrl:
        st.markdown("**Control** `low`")
        st.write(entry['control_output'])
    st.success("A/B entry written to logs/ab_log.jsonl")
else:
    with st.spinner(f"Calling {CFG['model']['name']}…"):
        response = call_llm(user_input, mod.system_prompt, CFG)
    st.write(response)

st.divider()

# ── ⑤ Feed response back into the engine ─────────────────────────────────────

if response and response.strip():
    resp_concepts = extract(response)
    if resp_concepts:
        resp_texts = [c.text for c in resp_concepts]

        detect_and_log(response, resp_concepts, graph=g)
        g.update(resp_concepts)
        g.save()

        def _s(fn):
            try: fn()
            except Exception: pass

        _s(lambda: MetaState.get().reinforce(resp_texts))
        _s(lambda: __import__('memory').TemporalMemory.get().reinforce(resp_texts))
        _s(lambda: __import__('episodes').EpisodeStore.get().record(
            resp_texts, ambiguity=result.score, region='runner'))
        _s(lambda: __import__('predictor').Predictor.get().pre_activate(
            resp_texts, __import__('memory').TemporalMemory.get()))
        _s(lambda: __import__('contradiction').ContradictionRegistry.get().observe(resp_texts))
        _s(lambda: __import__('world_model').WorldModel.get().infer_from_context(resp_texts))
        _s(lambda: __import__('ecology').CognitiveEcology.get().tick(resp_texts))

        with st.expander(f"⑤ Response absorbed — {len(resp_concepts)} concepts learned"):
            st.caption("The engine's own answer was fed back as a lesson.")
            cols = st.columns(min(len(resp_concepts), 5))
            for i, c in enumerate(resp_concepts):
                cols[i % len(cols)].markdown(f"`{c.text}`")
