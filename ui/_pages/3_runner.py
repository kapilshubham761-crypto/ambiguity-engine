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

# ── Shared design system ──────────────────────────────────────────────────────

st.markdown("""
<style>
#MainMenu, footer, header[data-testid="stHeader"] { display: none !important; }
body, .stApp { background: #070709 !important; }
.block-container { padding: 1.5rem 2rem 3rem !important; max-width: 100% !important; }
*, *::before, *::after { font-family: 'Consolas', 'Courier New', monospace !important; }

[data-testid="stExpanderToggleIcon"],
[data-testid="stExpanderToggleIcon"] *,
[data-testid="stExpanderToggleIcon"] span,
[data-baseweb="icon"] span,
.material-icons, .material-symbols-rounded, .material-symbols-outlined,
span[class*="material-symbols"], span[class*="material-icons"],
[class*="MaterialIcon"], [class*="materialIcon"] {
    font-family: 'Material Symbols Rounded','Material Symbols Outlined','Material Icons',sans-serif !important;
    font-weight: normal !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-smoothing: antialiased !important;
}

/* Page title */
h1 {
    font-size: 22px !important;
    letter-spacing: 0.2em !important;
    text-transform: uppercase !important;
    color: #b0b0d8 !important;
    font-weight: 400 !important;
    margin-bottom: 4px !important;
}
/* Section headers */
h2, h3 {
    font-size: 12px !important;
    letter-spacing: 0.2em !important;
    text-transform: uppercase !important;
    color: #505078 !important;
    font-weight: 400 !important;
    border-bottom: 1px solid #222238 !important;
    padding-bottom: 6px !important;
    margin-top: 24px !important;
}
/* Body text */
p, li, .stMarkdown p { color: #9898c8 !important; font-size: 14px !important; }
caption, .stCaption { color: #505078 !important; font-size: 12px !important; letter-spacing: 0.06em !important; }

/* Metrics */
[data-testid="stMetric"] { background: #0a0a12; border: 1px solid #1a1a28; border-radius: 4px; padding: 10px 14px; }
[data-testid="stMetricLabel"] p { color: #505078 !important; font-size: 11px !important; letter-spacing: 0.15em !important; text-transform: uppercase !important; }
[data-testid="stMetricValue"] { color: #9898c8 !important; font-size: 28px !important; }

/* Text area */
textarea {
    background: #0a0a12 !important;
    border: 1px solid #222238 !important;
    color: #b0b0d8 !important;
    font-size: 14px !important;
}
textarea:focus { border-color: #4a4a70 !important; box-shadow: none !important; }

/* Select box */
[data-baseweb="select"] > div {
    background: #0a0a12 !important;
    border-color: #222238 !important;
    color: #9898c8 !important;
}

/* Divider */
hr { border-color: #222238 !important; margin: 20px 0 !important; }

/* Expanders */
[data-testid="stExpander"] { border: 1px solid #1a1a28 !important; background: #0a0a10 !important; }
[data-testid="stExpanderDetails"] { background: #07070a !important; }
summary[data-testid="stExpanderToggle"] span { color: #6868a0 !important; font-size: 13px !important; letter-spacing: 0.06em !important; }

/* Code blocks */
pre, code { background: #0d0d18 !important; color: #8080a8 !important; border: 1px solid #1a1a28 !important; font-size: 13px !important; }

/* Spinner */
[data-testid="stSpinner"] p { color: #505078 !important; font-size: 13px !important; }

/* Warning / info */
[data-testid="stAlert"] { background: #0a0a12 !important; border-color: #222238 !important; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="padding:16px 0 4px;border-bottom:1px solid #222238;margin-bottom:20px">
  <div style="font-size:22px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;font-weight:400">RUNNER</div>
  <div style="font-size:12px;color:#505078;letter-spacing:0.06em;margin-top:4px">send a prompt through the full pipeline</div>
</div>
""", unsafe_allow_html=True)

# ── Config ────────────────────────────────────────────────────────────────────

CFG = yaml.safe_load(open(os.path.join(ROOT, 'config.yaml')))

with st.sidebar:
    st.markdown('<div style="font-size:11px;letter-spacing:0.15em;text-transform:uppercase;color:#505078;margin-bottom:6px">Model</div>', unsafe_allow_html=True)
    _models = ['qwen2.5:3b-instruct', 'qwen2.5:7b-instruct', 'llama3.2:3b']
    _chosen = st.selectbox("Ollama model", _models,
                           index=_models.index(CFG['model']['name'])
                           if CFG['model']['name'] in _models else 0,
                           label_visibility='collapsed')
    CFG['model']['name'] = _chosen

# ── Input ─────────────────────────────────────────────────────────────────────

user_input = st.text_area(
    "Prompt",
    placeholder="Type anything — a question, a fragment, a tension…",
    height=100,
    label_visibility='collapsed',
)

run_btn = st.button("RUN", type="primary", width='stretch')

if not run_btn or not user_input.strip():
    st.stop()

# Heavy imports deferred — avoids torch circular import on page load
from graph     import SemanticGraph
from extractor import extract
from detector  import detect_and_log
from modulator import build_prompt, call_llm
from meta_state import MetaState

@st.cache_resource(show_spinner="Loading graph…")
def _load_graph():
    return SemanticGraph()

g = _load_graph()

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

st.markdown("### 1. Concepts extracted")
if concepts:
    tags = ''.join(
        f'<span style="display:inline-block;background:#0d0d18;border:1px solid #222238;'
        f'color:#9898c8;padding:3px 10px;margin:3px 4px;font-size:13px;letter-spacing:0.04em">'
        f'{c.text}<span style="color:#505078;margin-left:6px;font-size:11px">{c.source}</span></span>'
        for c in concepts
    )
    st.markdown(f'<div style="margin:12px 0">{tags}</div>', unsafe_allow_html=True)
else:
    st.markdown('<div style="color:#ff6644;font-size:13px;padding:8px 0">No concepts extracted.</div>', unsafe_allow_html=True)

st.divider()

# ── ② Ambiguity ───────────────────────────────────────────────────────────────

LEVEL_COL = {'low': '#44ff88', 'medium': '#ffaa44', 'high': '#ff4444'}
lc = LEVEL_COL.get(result.level, '#9898c8')

st.markdown("### 2. Ambiguity")
st.markdown(f"""
<div style="display:flex;align-items:center;gap:20px;margin:10px 0 16px">
  <div>
    <div style="font-size:11px;color:#505078;letter-spacing:0.15em;text-transform:uppercase">Score</div>
    <div style="font-size:36px;color:{lc};line-height:1">{result.score:.3f}</div>
  </div>
  <div style="font-size:22px;color:{lc};letter-spacing:0.15em;border:1px solid {lc}44;padding:4px 14px">{result.level.upper()}</div>
</div>
""", unsafe_allow_html=True)

m1, m2, m3 = st.columns(3)
m1.metric("Variance", f"{result.variance:.3f}")
m2.metric("Cluster",  f"{result.cluster:.3f}")
m3.metric("Bridge",   f"{result.bridge:.3f}")

st.divider()

# ── ③ Modulation ──────────────────────────────────────────────────────────────

st.markdown("### 3. Modulation")
st.markdown(f"""
<div style="display:flex;gap:24px;font-size:13px;margin:10px 0">
  <div><span style="color:#505078;text-transform:uppercase;letter-spacing:0.12em;font-size:11px">Level</span>&nbsp;
       <span style="color:#9898c8">{mod.level}</span></div>
  <div><span style="color:#505078;text-transform:uppercase;letter-spacing:0.12em;font-size:11px">Model</span>&nbsp;
       <span style="color:#9898c8">{CFG['model']['name']}</span></div>
</div>
""", unsafe_allow_html=True)
if mod.neighbours:
    items = ', '.join(f'<span style="color:#7878a8">{n}</span>' for n in mod.neighbours)
    st.markdown(f'<div style="font-size:13px;margin:4px 0"><span style="color:#505078">neighbours</span> {items}</div>', unsafe_allow_html=True)
if mod.meta_concepts:
    items = ', '.join(f'<span style="color:#8b5cf6">{c}</span>' for c in mod.meta_concepts)
    st.markdown(f'<div style="font-size:13px;margin:4px 0"><span style="color:#505078">meta pressure</span> {items}</div>', unsafe_allow_html=True)
with st.expander("System prompt sent to LLM"):
    st.code(mod.system_prompt, language=None)

st.divider()

# ── ④ LLM output ──────────────────────────────────────────────────────────────

st.markdown("### 4. LLM output")

with st.spinner(f"Calling {CFG['model']['name']}…"):
    response = call_llm(user_input, mod.system_prompt, CFG)

st.markdown(f'<div style="background:#0a0a12;border:1px solid #1a1a28;padding:16px 20px;font-size:14px;color:#b0b0d8;line-height:1.7;margin:10px 0;white-space:pre-wrap">{response}</div>', unsafe_allow_html=True)

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

        tags = ''.join(
            f'<span style="display:inline-block;background:#0d0d18;border:1px solid #222238;'
            f'color:#7878a8;padding:2px 8px;margin:2px 3px;font-size:12px">{c.text}</span>'
            for c in resp_concepts
        )
        with st.expander(f"5. Response absorbed — {len(resp_concepts)} concepts learned"):
            st.markdown('<div style="font-size:12px;color:#505078;margin-bottom:8px">The engine\'s own answer was fed back as a lesson.</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="margin:8px 0">{tags}</div>', unsafe_allow_html=True)
