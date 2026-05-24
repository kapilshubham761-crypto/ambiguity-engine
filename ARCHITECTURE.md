# Ambiguity Engine — Cognitive Architecture
*Last updated: 2026-05-24 · V7*

---

## What It Is

A fully autonomous, local AI that teaches itself by continuously fetching real content from the internet, extracting semantic concepts, building a knowledge graph, and evolving its own personality traits — with no human review required.

- No cloud AI. No paid APIs. Runs entirely on one machine.
- Python 3.14 · torch 2.12.0+cpu · Streamlit 1.x
- LLM (optional): Qwen 2.5 / Llama 3.2 via Ollama at localhost:11434

---

## End-to-End Flow

```
INTERNET SOURCES
  Wikipedia · arXiv · Gutenberg · Reddit · OpenAlex · Web
        │
        ▼
  [learner.py] AutoLearner
  Background thread · 10s cycles · 8 parallel workers · 12 topics/cycle
  Wikipedia weighted 3× over other sources
        │ sentences
        ▼
  [extractor.py]
  spaCy (en_core_web_sm) → noun chunks + named entities
  SentenceTransformer (all-MiniLM-L6-v2) → 384-dim embeddings
        │ Concept(text, embedding, source)
        ▼
  [detector.py]
  3-metric ambiguity score: variance · cluster · bridge
        │
        ├──► [graph.py] SemanticGraph
        │    NetworkX (RAM) + SQLite (data/graph.db)
        │    merge if cosine ≥0.85, edge reinforce ×1.10
        │
        ├──► [meta_state.py] MetaState
        │    500-concept activation pool · exp decay
        │
        ├──► [memory.py] TemporalMemory
        │    working → episodic → semantic (3 layers)
        │
        ├──► [episodes.py] EpisodeStore
        │    transition graph (directed, weighted)
        │
        ├──► [contradiction.py] ContradictionRegistry
        ├──► [world_model.py] WorldModel
        └──► [ecology.py] CognitiveEcology
             orchestrates all 13 subsystems per tick

        Every 3h:
        ├──► [abstractor.py] co-occurrence → abstract concepts
        └──► [worldview.py] longitudinal identity snapshot
```

---

## Architecture Layers

```
╔══════════════════════════════════════════════════════════════════════════╗
║                        AMBIGUITY ENGINE V7                               ║
║                    Cognitive Architecture — 10 Layers                    ║
╚══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 0 — INPUT                                                         │
│  [learner.py] AutoLearner                                                │
│  · 10s cycle, 8 workers, 12 topics/cycle                                 │
│  · TOPICS[46] flat list (no curriculum stages)                           │
│  · Wikipedia 3× weighted over other sources                              │
│  · Thread-safe _feed_append (Lock) — 8 workers write live_feed.jsonl     │
│  · Writes cog_status.json after each cycle (mode + goal for UI/overlay)  │
│  · paused.txt: "1" = pause everything, "0" = run                        │
│  [sources.py] fetch_content() per source type                            │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — EXTRACTION                                                    │
│  [extractor.py]                                                          │
│  · import torch FIRST (prevents sentence_transformers circular import)   │
│  · spaCy en_core_web_sm → noun chunks + named entities → normalise       │
│  · SentenceTransformer all-MiniLM-L6-v2 → 384-dim embeddings            │
│  · In-memory session cache (concept text → embedding)                    │
│                                                                          │
│  [detector.py]                                                           │
│  · variance: pairwise cosine distance across concept embeddings          │
│  · cluster:  k=2 centroid separation                                     │
│  · bridge:   graph neighbourhood pull                                    │
│  · feeds TensionTracker · NoveltyTracker                                 │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — KNOWLEDGE STORE                                               │
│  [graph.py] SemanticGraph                                                │
│  · NetworkX (in-memory) + SQLite data/graph.db                          │
│  · merge node if cosine ≥0.85, else new node                            │
│  · edge weight ×1.10 on co-occurrence                                   │
│  · daily snapshots → snapshots/*.json                                    │
│                                                                          │
│  [episodes.py] EpisodeStore                                              │
│  · record(concepts) → data/episodes.jsonl                               │
│  · directed transition graph → data/transitions.json                    │
│  · cooccurrence_matrix() feeds Abstractor                                │
└──────────┬─────────────────────────┬────────────────────────────────────┘
           │                         │
           ▼                         ▼
┌──────────────────┐    ┌────────────────────────────┐
│  [predictor.py]  │    │  [abstractor.py]  (3h)      │
│  Predictor       │    │  Co-occurrence clusters →    │
│  transitions →   │    │  abstract concepts L0/L1/L2  │
│  pre-activates   │    │  data/abstractions.json      │
│  next concepts   │    └────────────────────────────┘
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — ATTENTION / WORKING MEMORY                                    │
│  [meta_state.py] MetaState                                               │
│  · 500-concept activation pool                                           │
│  · A1: sigmoid saturation · A2: fatigue (repetition penalty)             │
│  · A3: hot-concept cooling · C: regional spatial pools                   │
│  · data/meta_state.json                                                  │
│                                                                          │
│  [memory.py] TemporalMemory                                              │
│  · working  (decay 0.97/min)  → episodic on repeated hit                │
│  · episodic (decay 0.9997/min) → semantic on sustained activation        │
│  · semantic (decay 0.99997/min) = permanent long-term store              │
│  · data/memory.json                                                      │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — REASONING                                                     │
│  [contradiction.py]  bidirectional conflict detection  contradictions.json│
│  [world_model.py]    causal edges (causes|predicts|…)  world_model.json  │
│  [novelty.py]        score=1/log(seen+e), anti-loop     novelty.json     │
│  [tension.py]        cross-cutting ambiguity pressure   (in-memory)      │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — STABILITY / MODE                                              │
│  [stability.py] StabilityMonitor                                         │
│  Shannon entropy over MetaState pool → 5 cognitive modes:                │
│  focused · exploitative · exploratory · associative · reflective         │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 6 — GOAL / DRIVE                                                  │
│  [goals.py] GoalEngine — 5 competing drives (argmax → current_goal):    │
│  reduce_uncertainty 0.30 · increase_novelty 0.25                        │
│  resolve_contradiction 0.20 · maintain_stability 0.15                   │
│  expand_regions 0.10                                                     │
│                                                                          │
│  [energy.py] EnergyBudget                                                │
│  pool 1.0, replenish 0.08/tick                                           │
│  costs: sim 0.04 · explore 0.06 · abstract 0.10 · region_switch 0.05    │
│  data/energy.json                                                        │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 7 — SELF-MODEL / IDENTITY                                         │
│  [self_model.py]   recursive self-prediction accuracy   self_model.json  │
│  [identity.py]     5 slowly-drifting personality traits identity.json    │
│    exploration_style · novelty_bias · stability_bias                     │
│    abstraction_depth · contradiction_tolerance                           │
│  [worldview.py]    longitudinal 5-dimension snapshot (3h) worldview.json │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 8 — META / ADAPTATION                                             │
│  [reflection.py]    unified self-report + pathology detection            │
│  [meta_learning.py] strategy scores via prediction accuracy windows      │
│  [evolver.py]       hill-climbing: novelty_strength + bias_strength      │
│  [ecology.py]       orchestration heartbeat — sequences all 13 subsystems│
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 9 — META-REGULATION (NEW in V7)                                   │
│  [regulator.py] MetaRegulator                                            │
│  · Background thread, 60s interval                                       │
│  · Reads 7 live system metrics:                                          │
│    entropy · contradiction_rate · novelty_saturation · loop_score        │
│    energy · activation_spread · ambiguity_pressure                       │
│  · Computes targets for 12 Layer 2 variables using weighted signals      │
│  · Smooth exponential blend (α=0.12): new = old×0.88 + target×0.12      │
│  · Hard bounds enforced per variable (see _BOUNDS dict)                  │
│  · Writes to engine_config.json · sets _regulated=True flag              │
│  · Never touches Layer 1 physics constants                               │
│                                                                          │
│  Variable Taxonomy:                                                      │
│  Layer 1 — Physics (manual sliders in YTP): energy costs, evolver delta  │
│  Layer 2 — Adaptive (MetaRegulator, fully automated):                    │
│    attention: novelty_strength · bias_strength                           │
│    meta_state: decay_rate · reinforce_gain · fatigue_k · cooling_alpha   │
│    identity: drift_rate                                                  │
│    goals: reduce_uncertainty · increase_novelty · resolve_contradiction  │
│            maintain_stability · expand_regions                           │
│  Layer 3 — Personality (slow drift, IdentityTracker/Evolver):            │
│    5 identity traits over hours/days                                     │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  LLM LAYER — Runner page (manual / interactive)                          │
│  [modulator.py] → Ollama (localhost:11434)                               │
│  low    ambiguity → bare system prompt                                   │
│  medium ambiguity → + top-5 graph neighbours injected                   │
│  high   ambiguity → + tension framing + 8 neighbours + meta pressure     │
│  Step 5: LLM response fed back into full pipeline                        │
│  (engine learns from its own answers)                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Module Reference

| Module | Class | Purpose | Data File |
|---|---|---|---|
| learner.py | AutoLearner | Main loop — search, fetch, orchestrate. Background subsystem thread (30s), regulator thread (60s). Writes currently_reading.json at start of each fetch for live UI signal. | live_feed.jsonl, learner_stats.json, fetch_status.json, cog_status.json, currently_reading.json, perf_profile.json |
| sources.py | — | Multi-source search + fetch per source type | — |
| extractor.py | Concept | spaCy NLP + MiniLM 384-dim embeddings | — (session cache) |
| detector.py | AmbiguityResult | 3-metric ambiguity scoring. Variance: vectorized via matrix multiply (normed @ normed.T). Bridge: uses graph._emb_matrix pre-built cache (one BLAS call, no rebuild). KMeans n_init=3. | logs/ambiguity_scores.jsonl |
| modulator.py | ModulationResult | Graph-aware prompt builder + Ollama call | — |
| graph.py | SemanticGraph | NetworkX + SQLite knowledge store. _emb_pending batch queue → _flush_pending() does single vstack per update() call. Pre-built _emb_matrix + _emb_node_ids for fast bridge metric. | graph.db |
| episodes.py | EpisodeStore | Episode log + directed transition graph | episodes.jsonl, transitions.json |
| predictor.py | Predictor | Anticipatory pre-activation from transitions | — |
| abstractor.py | Abstractor | Co-occurrence clusters → abstract concepts | abstractions.json |
| meta_state.py | MetaState | 500-concept attention pool + decay | meta_state.json |
| memory.py | TemporalMemory | 3-layer temporal memory store | memory.json |
| contradiction.py | ContradictionRegistry | Bidirectional conflict detection | contradictions.json |
| world_model.py | WorldModel | Directed causal edge registry | world_model.json |
| novelty.py | NoveltyTracker | Exposure count + anti-loop detection | novelty.json |
| tension.py | TensionTracker | Cross-cutting ambiguity/conflict pressure | in-memory |
| stability.py | StabilityMonitor | Shannon entropy → 5 cognitive modes | — |
| goals.py | GoalEngine | 5 competing intrinsic drives | — |
| energy.py | EnergyBudget | Finite energy pool per activity | energy.json |
| self_model.py | SelfModel | Recursive self-prediction accuracy | self_model.json |
| identity.py | IdentityTracker | 5 slowly-drifting personality traits | identity.json |
| worldview.py | Worldview | Longitudinal identity (5 dimensions, 3h) | worldview.json |
| reflection.py | ReflectionMonitor | Unified self-report + pathology detection | reflection.json |
| meta_learning.py | MetaLearner | Strategy scoring via prediction accuracy | meta_learning.json |
| evolver.py | Evolver | Hill-climbing parameter adaptation | evolved_params.json |
| ecology.py | CognitiveEcology | Orchestration heartbeat (13 subsystems) | — |
| config.py | Config | Live config reader with mtime cache | engine_config.json |
| regulator.py | MetaRegulator | Layer 2 autonomous parameter regulation — reads 7 system metrics, computes targets for 12 variables, smooth blend α=0.12, 60s interval | engine_config.json |

---

## UI Pages

All pages use underscore prefix (`ui/_pages/`) — never `ui/pages/` (Streamlit would auto-discover them and create duplicate nav).

| File | Nav Title | Purpose |
|---|---|---|
| 1_state.py | Core | Cognitive Core Panel — real-time machine state monitor. Custom HTML/CSS layout. Auto-refreshes every 4s. Boots AutoLearner via st.cache_resource. |
| 3_runner.py | Runner | Manual prompt pipeline: extract → detect → modulate → LLM → feed back. Heavy imports (extractor, torch) deferred after st.stop() guard to prevent circular import on page load. |
| 0_meta_state.py | Meta-State | Live concept activation pool — bars, mood, decay health. Auto-refresh 30s. |
| 10_cognition.py | Cognition | 8-tab deep dive: Live · Memory · Episodes · Predictions · Contradictions · Abstractions · Simulate · Worldview |
| 11_config.py | Settings | Live-editable engine params, ego presets (max 3), Cloudflare tunnel toggle. |
| 12_ytp.py | YTP | Your Tuning Panel. Layer 1 sliders (6 physics constants). Layer 2 read-only live display (12 variables with AUTO badges, shown only after _regulated=True). Local-only access gate (blocks Cloudflare proxy). |

### app.py (entry point)
- `st.set_page_config(layout="wide", initial_sidebar_state="expanded")`
- `page_icon=PIL.Image.open("ui/assets/logo.png")` — project logo as favicon
- Boot splash: 4s animated overlay, `sessionStorage` key prevents replay
- Status detection: reads `cog_status.json`, `fetch_status.json`, `paused.txt`
- Live bar: reads `currently_reading.json` first (written at START of fetch = real-time); falls back to live_feed.jsonl (written at END of fetch)
- Sidebar: Stop/Resume button (writes paused.txt), animated status dot, thinking line, goal/mode chip
- Global CSS injected from **main page context** (not sidebar.markdown) so it applies even when sidebar is collapsed
- Pre-loads `torch` at startup to prevent circular import in Runner page
- `st.navigation(pages)` — explicit nav, no icons (clean)

---

## Design System

Applied consistently across all pages via injected `<style>` blocks.

```
Background:   #070709  (page)  /  #09090f (sidebar)  /  #0a0a12 (panels)
Borders:      #222238  (primary)  /  #1a1a28 (subtle)
Font:         Consolas, Courier New, monospace — applied to * via !important
              Material Icons font restored for expander icons

Text colors:
  #b0b0d8   body / primary values
  #9898c8   secondary values
  #7878a8   dim values
  #6868a0   keys / labels
  #505078   section headers / captions (12px uppercase, letter-spacing 0.2em)
  #606090   timestamps / metadata
  #4a4a70   separators

Accent colors:
  #4a9eff   blue  (status, links, active nav, expand button)
  #44ff88   green (running, ok)
  #ff4444   red   (paused, error, high ambiguity)
  #f59e0b   amber (focused mode)
  #8b5cf6   purple (abstractions, meta)
  #10b981   teal  (reflective mode)

Page title:   22px, letter-spacing 0.2em, uppercase, #b0b0d8, font-weight 400
Section h2:   12px, letter-spacing 0.2em, uppercase, #505078, border-bottom #222238
Streamlit header hidden: header[data-testid="stHeader"] { display: none !important }
```

---

## Data Files

```
data/
├── graph.db               SemanticGraph   — SQLite (nodes + edges, primary knowledge store)
├── live_feed.jsonl        AutoLearner     — last 200 activity entries (UI event stream)
├── learner_stats.json     AutoLearner     — total_sentences, total_concepts, session history
├── fetch_status.json      AutoLearner     — {fetching: bool, started_at: ISO}
├── cog_status.json        AutoLearner     — {mode, goal} written each cycle (sidebar chip, overlay)
├── paused.txt             cross-process   — "1" = paused, "0" = running
├── engine_config.json     Config          — all live params + ego presets
├── meta_state.json        MetaState       — activation pool snapshot
├── memory.json            TemporalMemory  — 3-layer memory snapshot
├── energy.json            EnergyBudget    — current pool level + spent count
├── identity.json          IdentityTracker — 5 personality trait values
├── contradictions.json    ContradictionRegistry
├── world_model.json       WorldModel      — causal edge registry
├── novelty.json           NoveltyTracker  — exposure counts + escape concepts
├── self_model.json        SelfModel       — prediction accuracy history
├── worldview.json         Worldview       — longitudinal identity snapshot
├── reflection.json        ReflectionMonitor — last self-report
├── meta_learning.json     MetaLearner     — strategy scores
├── evolved_params.json    Evolver         — adapted hill-climbed parameters
├── episodes.jsonl         EpisodeStore    — concept co-occurrence episode log
├── transitions.json       EpisodeStore    — directed transition weights
├── abstractions.json      Abstractor      — abstract concept hierarchy L0/L1/L2
├── currently_reading.json AutoLearner     — {title, source, url, concepts_found} written at START of each fetch (live "what is it reading now" signal for UI)
└── perf_profile.json      AutoLearner     — pipeline timing profile: graph_update, subsystems, detect, total (ms) — feeds Cognition tab efficiency graph

snapshots/  — SemanticGraph daily node+edge snapshots (*.json)
logs/
├── ambiguity_scores.jsonl — detector.py per-sentence ambiguity log
└── (ab_log.jsonl removed — A/B testing feature removed)
```

---

## AutoLearner Detail

```python
# learner.py key constants (code constants — not editable via YTP)
CYCLE_TIME       = 10      # seconds between cycles
N_WORKERS        = 8       # ThreadPoolExecutor size
TOPICS_PER_CYCLE = 12      # topics sampled per cycle
FETCH_TIMEOUT    = 12      # per-fetch timeout (seconds)
SEARCH_TIMEOUT   = 7       # per-search timeout (seconds)
FEED_MAX         = 200     # max entries in live_feed.jsonl
CHECKIN_EVERY    = 10800   # 3h subsystem checkin interval

# Source weighting
_src_weights = [3 if s == 'wikipedia' else 1 for s in all_sources]
src = random.choices(all_sources, weights=_src_weights, k=1)[0]

# Thread safety
self._feed_lock = threading.Lock()  # prevents 8 workers corrupting live_feed.jsonl

# Background threads (started alongside main worker pool):
_subsys_worker()     — drains _subsys_buf every 30s (subsystems run off critical path)
_regulator_worker()  — calls MetaRegulator.tick() every 60s

# Per-article pipeline:
1. Write currently_reading.json at START of fetch (live UI signal)
2. detect() called ONCE per article on sample of ≤30 concepts (not per-sentence)
3. Subsystem updates queued to _subsys_buf (non-blocking)
4. Timing profile recorded → perf_profile.json

# After each cycle, writes:
data/cog_status.json  → {mode, goal}  (read by sidebar chip + overlay)
```

---

## Pipeline Optimizations (V7)

Three bottlenecks identified and resolved:

| Bottleneck | Before | Fix |
|---|---|---|
| Subsystems (13 modules) | 78s synchronous per article | Background 30s thread draining a buffer — zero cost on hot path |
| Graph embedding vstack | 62s — np.vstack per new node in tight loop | _emb_pending list + _flush_pending() — single vstack per update() call |
| Bridge metric | Part of 31s detect — rebuilt 4473-node array every sentence | Uses graph._emb_matrix pre-built cache + one BLAS matrix-vector multiply |
| Variance metric | O(n²) loop | Vectorized: normed @ normed.T, np.triu_indices for upper triangle |
| detect() call frequency | Once per sentence | Once per article on ≤30-concept sample |

---

## Standalone Visualizer (visualizer.py / visualizer.bat)

Separate Streamlit app on port 8502 — knowledge graph explorer independent of main UI.

- `visualizer.bat` — one-click launch: kills old, starts on port 8502, polls health, opens browser
- Graphs: 2D scatter (UMAP/PCA), 3D force layout, neighbourhood explorer, timeline
- All charts full-height: `height = calc(100vh - 160px)` CSS + `_FULL_H = 820` constant
- Refresh is manual only (no auto-refresh meta tag — would reset Streamlit session state)

---

## Overlay (overlay.py)

Always-on-top tkinter window. Launched separately via `overlay.bat` or `launch.bat`.

```
Sections: SYSTEM (CPU/RAM/GPU/TEMP) · GRAPH (nodes/edges/sentences/growth)
          COGNITION (mode/goal from cog_status.json)
Sizes:    FULL_H=290 (expanded) · COMPACT_H=28 (collapsed, click to toggle)
GPU:      pynvml — load% · VRAM used · temperature
```

---

## LLM Integration (Ollama)

```yaml
# config.yaml
model:
  name:     qwen2.5:3b-instruct
  endpoint: http://localhost:11434/api/generate
  stream:   false

# Available models:
qwen2.5:3b-instruct    — default, fast
qwen2.5:7b-instruct    — smarter, slower
llama3.2:3b            — alternative
```

---

## Ego System

```
Max 3 named personality presets stored in engine_config.json["egos"]
Each ego captures 5 sections: identity · evolver · meta_state · goals · attention
UI: dropdown → Load / Delete  ·  text input → Save Ego
Reset to defaults preserves egos.

engine_config.json structure:
{
  "active_ego": "Curious",
  "egos": {
    "Curious": { "identity": {...}, "evolver": {...}, "meta_state": {...}, ... }
  },
  "learning":   { "cycle_time": 10, "n_workers": 8, ... },
  "energy":     { "replenish_per_tick": 0.08, ... },
  "identity":   { "drift_rate": 0.02 },
  "evolver":    { "adapt_every": 30, "max_delta": 0.05 },
  "attention":  { "bias_strength": 0.0, "novelty_strength": 0.0 },
  "meta_state": { "decay_rate": 0.97, "reinforce_gain": 0.25, ... },
  "goals":      { "reduce_uncertainty": 0.30, ... }
}
```

---

## Cognitive Modes

| Mode | Entropy | Behaviour |
|---|---|---|
| focused | low, stable | Deep exploitation of current concepts |
| exploitative | medium, repeating | Reinforcing known high-value paths |
| exploratory | high, searching | Broad search across new topics |
| associative | mid, bridging | Connecting distant concept clusters |
| reflective | low, self-checking | Internal audit — runs ReflectionMonitor |

## Memory Layers

| Layer | Decay Rate | Promotion Condition |
|---|---|---|
| working | 0.97/min | Repeated reinforcement hits |
| episodic | 0.9997/min | Sustained activation over time |
| semantic | 0.99997/min | Permanent long-term store |

## Scaling

```
Safe zone     <50k nodes    <1 GB RAM, everything fast
Manageable    50–120k nodes 1–3 GB RAM, startup slows
Danger zone   >150k nodes   RAM pressure, prune aggressively
Hard limit    ~300k nodes   process OOM risk

Bottlenecks:
  NetworkX full graph in RAM     → use SQLite queries where possible
  episodes.jsonl unbounded       → add rolling 30-day window
  embedding cache unbounded dict → cap at 50k LRU
```

---

## Known Issues / Gotchas

- **torch circular import**: Fixed by (1) `import torch` at top of `extractor.py`, (2) pre-loading torch in `app.py` main thread before `pg.run()`, (3) deferring all ML imports in `runner.py` to after `st.stop()` guard. Root cause was `cupy-cuda12x` installed without `pytest` — uninstalled.
- **fileWatcherType = none**: Set in `ui/.streamlit/config.toml` (must be in `ui/` dir, not root). Prevents WinError 206 from torch DLL paths exceeding Windows 260-char MAX_PATH limit. Code changes require manual Streamlit restart.
- **Sidebar CSS**: Must be injected from `st.markdown()` (main page), NOT `st.sidebar.markdown()`. When sidebar is collapsed, sidebar DOM is not rendered, so CSS in sidebar.markdown never reaches the page.
- **Page icons**: `st.Page()` icon arg requires a real emoji or None. `st.set_page_config(page_icon=)` accepts a PIL Image. Current nav uses no icons (clean text only).
- **No coding features**: Never add git panels, terminals, or code editors to the Streamlit app. User codes in VS Code.
- **launch.bat**: Does NOT open `ambiguity-engine-tracker.html` — that line was removed.
- **np.stack dtype**: `np.stack()` does NOT accept a `dtype` parameter (only `axis`, `out`). Use `np.array(list, dtype=...)` instead. Silent crash otherwise.
- **YTP local-only gate**: Cloudflare rewrites Host header to `localhost:8501` so host check alone is insufficient. Gate checks CF-Ray, CF-Connecting-IP, X-Forwarded-For, X-Forwarded-Host headers — any of these present = block.
- **perf_profile.json**: Written by AutoLearner after each article. If engine has not run since code update, file will be absent and UI shows "warming up" — restart engine with restart.bat.
- **currently_reading.json**: Written at START of fetch (not end). If engine is paused or just started, file may be absent — UI falls back to live_feed.jsonl gracefully.
