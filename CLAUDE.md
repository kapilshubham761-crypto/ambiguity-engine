# Ambiguity Engine — Project Context for LLMs

## What This Is

A fully autonomous, local AI learning system that teaches itself by continuously fetching real content from the internet, extracting semantic concepts, and building a knowledge graph. No human review required. No cloud AI. No paid APIs. Runs entirely on the user's machine.

**Python 3.14 · torch 2.12.0+cpu · Streamlit · spaCy · SentenceTransformers · Ollama**

---

## How It Works (end-to-end)

1. **AutoLearner** (`src/learner.py`) runs background threads 24/7 — 10s cycles, 8 workers, 12 topics/cycle
2. Fetches from Wikipedia (3× weighted), arXiv, Gutenberg, Reddit, OpenAlex, Web
3. Extracts concepts via spaCy + MiniLM 384-dim embeddings (`src/extractor.py`)
4. Updates semantic graph (`src/graph.py` — NetworkX + SQLite), memory, meta-state, contradiction registry, world model, ecology
5. Every 3 hours: abstractor clusters concepts into abstract nodes; worldview takes longitudinal snapshot
6. **Runner page** (`ui/_pages/3_runner.py`): manual prompt → full pipeline → LLM via Ollama → response fed back into engine
7. **Overlay** (`overlay.py`): always-on-top tkinter window with CPU/RAM/GPU/graph stats

---

## Project Structure

```
ambiguity-engine/
├── src/
│   ├── learner.py         AutoLearner — main background loop (replaces old Teacher)
│   ├── sources.py         Multi-source search + fetch
│   ├── extractor.py       spaCy NLP + SentenceTransformer embeddings (import torch FIRST)
│   ├── detector.py        3-metric ambiguity scoring (variance/cluster/bridge)
│   ├── modulator.py       Graph-aware prompt builder + Ollama LLM call
│   ├── graph.py           SemanticGraph — NetworkX + SQLite (graph.db)
│   ├── episodes.py        Episode log + directed transition graph
│   ├── predictor.py       Anticipatory pre-activation from transitions
│   ├── abstractor.py      Co-occurrence clusters → abstract concepts (3h)
│   ├── meta_state.py      MetaState — 500-concept attention pool + decay
│   ├── memory.py          TemporalMemory — 3-layer store (working/episodic/semantic)
│   ├── contradiction.py   ContradictionRegistry — bidirectional conflict detection
│   ├── world_model.py     WorldModel — directed causal edges
│   ├── novelty.py         NoveltyTracker — exposure count + anti-loop
│   ├── tension.py         TensionTracker — cross-cutting ambiguity pressure
│   ├── stability.py       StabilityMonitor — Shannon entropy → 5 cognitive modes
│   ├── goals.py           GoalEngine — 5 competing intrinsic drives
│   ├── energy.py          EnergyBudget — finite energy pool
│   ├── self_model.py      SelfModel — recursive self-prediction accuracy
│   ├── identity.py        IdentityTracker — 5 drifting personality traits
│   ├── worldview.py       Worldview — longitudinal identity (3h)
│   ├── reflection.py      ReflectionMonitor — self-report + pathology detection
│   ├── meta_learning.py   MetaLearner — strategy scoring
│   ├── evolver.py         Evolver — hill-climbing parameter adaptation
│   ├── ecology.py         CognitiveEcology — orchestration heartbeat (13 subsystems)
│   └── config.py          Config — live config reader with mtime cache
├── ui/
│   ├── app.py             Streamlit entry point — boot splash, sidebar, nav, global CSS
│   ├── .streamlit/
│   │   └── config.toml    fileWatcherType=none (prevents WinError 206 from torch DLLs)
│   ├── assets/logo.png
│   ├── components/status.py
│   └── _pages/            Pages use underscore prefix — NOT auto-discovered by Streamlit
│       ├── 1_state.py     Core — Cognitive Core Panel (main dashboard, auto-refresh 4s)
│       ├── 3_runner.py    Runner — manual pipeline + LLM
│       ├── 0_meta_state.py Meta-State — concept activation pool
│       ├── 10_cognition.py Cognition — 8-tab deep cognitive view
│       └── 11_config.py   Settings — live params, egos, Cloudflare tunnel
├── data/                  Runtime files (gitignored) — see Data Files below
├── overlay.py             Always-on-top tkinter stats (CPU/RAM/GPU/graph/mode/goal)
├── launch.bat             Kill old → clear caches → start overlay + Streamlit → open browser
├── restart.bat            Quick restart (no overlay, no browser)
├── overlay.bat            Launch overlay only (pythonw, no console)
├── config.yaml            LLM config (model name, Ollama endpoint)
├── CLAUDE.md              This file
└── ARCHITECTURE.md        Full architecture with layer diagrams, module ref, data files
```

---

## Key Conventions

- **Always use `.venv/Scripts/python`** — not system python
- **Pages must be in `ui/_pages/`** — underscore prefix prevents Streamlit auto-discovery
- **paused.txt** — write `"1"` to pause everything, `"0"` to resume; read live on every call
- **import torch FIRST** in `extractor.py` — prevents sentence_transformers → transformers → torch circular import
- **Deferred imports in runner.py** — all ML imports (`from extractor import extract` etc.) are after `st.stop()` guard so they only load when Run button is clicked
- **Global CSS in `st.markdown()`** not `st.sidebar.markdown()` — sidebar CSS must be in main page context or it won't apply when sidebar is collapsed
- **fileWatcherType = none** in `ui/.streamlit/config.toml` — code changes require Streamlit restart (run restart.bat)
- **No coding features in the Streamlit app** — no git panels, terminals, code editors. User codes in VS Code
- **launch.bat does NOT open** any HTML tracker files
- **cupy-cuda12x is uninstalled** — it was breaking imports (needed pytest which wasn't installed). Do not reinstall.

---

## Data Files (all in `data/`)

| File | Written by | Contains |
|---|---|---|
| graph.db | SemanticGraph | SQLite — nodes + edges (primary knowledge store) |
| live_feed.jsonl | AutoLearner | Last 200 activity entries (UI event stream) |
| learner_stats.json | AutoLearner | total_sentences, total_concepts, session history |
| fetch_status.json | AutoLearner | {fetching, started_at} for sidebar elapsed timer |
| cog_status.json | AutoLearner | {mode, goal} — written each cycle, read by overlay + sidebar |
| paused.txt | UI buttons | "1" paused / "0" running — cross-process sync |
| engine_config.json | Settings page | All live params + ego presets (max 3) |
| meta_state.json | MetaState | 500-concept activation pool snapshot |
| memory.json | TemporalMemory | 3-layer memory snapshot |
| energy.json | EnergyBudget | Current pool level + spent count |
| identity.json | IdentityTracker | 5 personality trait values |
| contradictions.json | ContradictionRegistry | Open + resolved contradictions |
| world_model.json | WorldModel | Causal edge registry |
| novelty.json | NoveltyTracker | Exposure counts + escape concepts |
| self_model.json | SelfModel | Prediction accuracy history |
| worldview.json | Worldview | Longitudinal identity snapshot (3h) |
| reflection.json | ReflectionMonitor | Last self-report |
| meta_learning.json | MetaLearner | Strategy scores |
| evolved_params.json | Evolver | Adapted hill-climbed parameters |
| episodes.jsonl | EpisodeStore | Concept co-occurrence episode log |
| transitions.json | EpisodeStore | Directed transition weights |
| abstractions.json | Abstractor | Abstract concept hierarchy L0/L1/L2 |

---

## UI Design System

All pages inject a consistent CSS block. The reference design is `1_state.py`.

```
Background:  #070709 (page)  #09090f (sidebar)  #0a0a12 (panels)
Borders:     #222238 (primary)  #1a1a28 (subtle)
Font:        Consolas, Courier New, monospace (!important everywhere)
             Material Icons font restored for [data-testid="stExpanderToggleIcon"]

Text:
  #b0b0d8  body / primary values
  #9898c8  secondary values
  #7878a8  dim values
  #6868a0  keys / labels
  #505078  section headers (12px uppercase letter-spacing:0.2em)
  #4a4a70  separators

Accents:
  #4a9eff  blue  (active, links, expand button)
  #44ff88  green (running / ok)
  #ff4444  red   (paused / error)
  #f59e0b  amber (focused mode)
  #8b5cf6  purple (meta / abstractions)

Page title:  22px, uppercase, letter-spacing:0.2em, color:#b0b0d8, font-weight:400
Sections h2: 12px, uppercase, letter-spacing:0.2em, color:#505078, border-bottom:#222238
Streamlit header hidden: header[data-testid="stHeader"] { display:none !important }
No emojis anywhere in the UI.
```

---

## Launching

```
# Full launch (recommended):
double-click launch.bat
  → kills old processes
  → clears __pycache__
  → starts overlay (pythonw)
  → starts Streamlit on port 8501
  → polls until healthy
  → opens http://localhost:8501

# Manual Streamlit only:
cd ui
..\.venv\Scripts\streamlit run app.py

# Overlay only:
double-click overlay.bat

# Quick restart (no overlay, no browser):
double-click restart.bat
```

---

## Current State (as of 2026-05-24)

**What works:**
- Fully autonomous learning — AutoLearner runs 24/7, no human review
- 4,473+ nodes, 18,410+ edges in graph.db
- All 9 cognitive layers active (learner → ecology heartbeat)
- Cognitive Core Panel (1_state.py) — real-time machine state monitor
- 5-page navigation: Core / Runner / Meta-State / Cognition / Settings
- Ego system (up to 3 personality presets)
- Cloudflare tunnel in Settings (optional public URL)
- GPU monitoring in overlay via pynvml

**Known issues:**
- Sidebar expand/collapse button styling: CSS must be in main page `st.markdown()` not sidebar context
- torch circular import: fully resolved via import ordering + cupy uninstall
- fileWatcherType=none: must restart Streamlit manually after code changes

---

## GitHub
https://github.com/kapilshubham761-crypto/ambiguity-engine
