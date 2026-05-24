# Ambiguity Engine — Cognitive Architecture

## Overview

8 layers · 24 modules · 20 data files  
Closed loop: raw text → concepts → graph → working memory → reasoning → stability → goals → identity → adaptation

**LLM:** Qwen 2.5 via Ollama (`localhost:11434`) — wired into Runner page  
**Graph layout:** UMAP on 384-dim embeddings (semantic proximity, not spiral)  
**GPU monitoring:** pynvml overlay (load % · VRAM · temp)  
**Ego system:** up to 3 named personality presets, saved in engine_config.json

---

## Quick Process Map (as shown on Dashboard)

```
 SOURCES          EXTRACTION          KNOWLEDGE            COGNITION             OUTPUT
 ───────          ──────────          ─────────            ─────────             ──────
 Wikipedia  ─┐                       SemanticGraph        MetaState             Runner
 arXiv      ─┤                       (NetworkX +          (attention pool)  ──► (Qwen /
 Gutenberg  ─┼─► fetch ─► sentences ─► SQLite)    ──────► TemporalMemory        Ollama)
 Reddit     ─┤    10s                 nodes merge          (3 layers)
 OpenAlex   ─┤    cycle               cosine≥0.85          Contradiction    ─┐
 Web        ─┘                                             WorldModel        │
                  spaCy NLP                                NoveltyTracker    │  graph.db
                  (noun chunks)      episodes.jsonl        StabilityMonitor  │  grows each
                  MiniLM embed  ───► transitions.json ───► GoalEngine   ─────┘  cycle
                  384-dim            Abstractor (3h)       EnergyBudget
                                                           SelfModel
                                                           IdentityTracker
                                                           Evolver  (adapt)
                                                           Reflection
                                                           CognitiveEcology
```

---

## Structure Map

```
╔══════════════════════════════════════════════════════════════════════════╗
║                        AMBIGUITY ENGINE                                  ║
║                    Cognitive Architecture Map                            ║
╚══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 0 — INPUT / DISCOVERY                                            │
│                                                                         │
│  [sources.py]                                                           │
│  Wikipedia · arXiv · Gutenberg · Reddit · OpenAlex · Web               │
│      │ raw text                                                         │
│      ▼                                                                  │
│  [learner.py] AutoLearner                                               │
│  Background thread · 10s cycles · 4 parallel workers                   │
│  Reads: TOPICS[46] → random search → fetch per source                  │
│  Writes: live_feed.jsonl · learner_stats.json · fetch_status.json       │
│          growth_log.jsonl · paused.txt                                  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ sentences
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — EXTRACTION                                                   │
│                                                                         │
│  [extractor.py]                                                         │
│  spaCy en_core_web_sm → noun chunks + named entities                   │
│  → normalise/lemmatise → deduplicate                                    │
│  → SentenceTransformer (all-MiniLM-L6-v2, 384-dim)                     │
│  Output: list[Concept(text, embedding, source)]                         │
│                                                                         │
│  [detector.py]                                                          │
│  3-metric ambiguity score on concept embeddings:                        │
│   • variance  (pairwise cosine distance)                                │
│   • cluster   (k=2 centroid separation)                                 │
│   • bridge    (graph neighbourhood pull)                                │
│  → feeds tension.TensionTracker · novelty.NoveltyTracker                │
│  Writes: logs/ambiguity_scores.jsonl                                    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ concepts + embeddings
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — KNOWLEDGE STORE                                              │
│                                                                         │
│  [graph.py] SemanticGraph                                               │
│  NetworkX (in-memory) + SQLite (data/graph.db)                         │
│  • update(concepts) — merge if cosine ≥0.85, else new node             │
│  • edge reinforce (weight × 1.10 on co-occurrence)                     │
│  • daily snapshots → snapshots/*.json                                   │
│                                                                         │
│  [episodes.py] EpisodeStore                                             │
│  • record(concepts) → data/episodes.jsonl                              │
│  • transition graph (directed, weighted) → data/transitions.json        │
│  • cooccurrence_matrix / strongest_paths for abstractor                │
└──────────┬──────────────────────────┬───────────────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────┐     ┌────────────────────────────┐
│  [predictor.py]  │     │  [abstractor.py]  (3h)     │
│  Predictor       │     │  Co-occurrence clusters →   │
│  transitions →   │     │  abstract concepts L0/L1/L2 │
│  pre-activates   │     │  Writes: abstractions.json  │
│  next concepts   │     └────────────────────────────┘
└────────┬─────────┘
         │ pre-activation
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — ATTENTION / WORKING MEMORY                                   │
│                                                                         │
│  [meta_state.py] MetaState                                              │
│  500-concept activation pool · exp decay                                │
│   A1: sigmoid saturation                                                │
│   A2: fatigue (repetition penalty)                                      │
│   A3: hot-concept cooling                                               │
│   C:  regional spatial pools                                            │
│   D1: normalisation                                                     │
│  Writes: data/meta_state.json                                           │
│                                                                         │
│  [memory.py] TemporalMemory                                             │
│  Three-layer store:                                                     │
│   working  (decay 0.97)   ──threshold──▶  episodic (0.9997)            │
│   episodic                ──threshold──▶  semantic (0.99997)           │
│  Writes: data/memory.json                                               │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — REASONING                                                    │
│                                                                         │
│  [contradiction.py] ContradictionRegistry                               │
│  Bidirectional conflict detection between active concepts               │
│  Writes: data/contradictions.json                                       │
│                                                                         │
│  [world_model.py] WorldModel                                            │
│  Directed causal edges: causes|suppresses|predicts|depends_on           │
│  Confidence decay; max 2000 edges                                       │
│  Writes: data/world_model.json                                          │
│                                                                         │
│  [novelty.py] NoveltyTracker                                            │
│  score = 1/log(times_seen + e)                                          │
│  Anti-loop: Jaccard overlap check → escape_concepts                     │
│  Writes: data/novelty.json                                              │
│                                                                         │
│  [tension.py] TensionTracker                                            │
│  Cross-cutting signal (conflict load, ambiguity pressure)               │
│  Read by: MetaState, Stability, Goals, Contradiction, Detector          │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — STABILITY / MODE                                             │
│                                                                         │
│  [stability.py] StabilityMonitor                                        │
│  Shannon entropy over MetaState activation pool                         │
│  5 cognitive modes:                                                     │
│   focused      — low entropy, stable                                    │
│   exploitative — medium entropy, repeating                              │
│   exploratory  — high entropy, searching                                │
│   associative  — mid entropy, bridging                                  │
│   reflective   — low entropy, self-checking                             │
│  Mode weights → modulate goal drives                                    │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 6 — GOAL / DRIVE                                                 │
│                                                                         │
│  [goals.py] GoalEngine                                                  │
│  5 competing drives (configurable weights):                             │
│   reduce_uncertainty    0.30  ← tension + contradiction load            │
│   increase_novelty      0.25  ← novelty tracker                        │
│   resolve_contradiction 0.20  ← contradiction registry                 │
│   maintain_stability    0.15  ← stability entropy                      │
│   expand_regions        0.10  ← region coverage                        │
│  argmax → current_goal                                                  │
│                                                                         │
│  [energy.py] EnergyBudget                                               │
│  Pool: 1.0, replenish 0.08/tick                                         │
│  Costs: simulation 0.04 · exploration 0.06 · abstraction 0.10          │
│  Writes: data/energy.json                                               │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 7 — SELF-MODEL / IDENTITY                                        │
│                                                                         │
│  [self_model.py] SelfModel                                              │
│  Recursive self-prediction (entropy mean-reversion)                     │
│  Prediction vs actual at horizon 5 ticks → accuracy                    │
│  Writes: data/self_model.json                                           │
│                                                                         │
│  [identity.py] IdentityTracker                                          │
│  5 slowly-drifting personality traits (drift 0.02/observe):            │
│   exploration_style · novelty_bias · stability_bias                     │
│   abstraction_depth · contradiction_tolerance                           │
│  Writes: data/identity.json                                             │
│                                                                         │
│  [worldview.py] Worldview  (3h refresh)                                 │
│  5 longitudinal dimensions:                                             │
│   concepts · contradictions · goals · home_regions · abstractions       │
│  Writes: data/worldview.json                                            │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 8 — META / ADAPTATION                                            │
│                                                                         │
│  [reflection.py] ReflectionMonitor                                      │
│  Reads all subsystems (non-invasive)                                    │
│  Detects: over_fixating · drifting · looping · stuck                   │
│  Writes: data/reflection.json                                           │
│                                                                         │
│  [meta_learning.py] MetaLearner                                         │
│  5 strategy scores via prediction accuracy windows                      │
│  Writes: data/meta_learning.json                                        │
│                                                                         │
│  [evolver.py] Evolver                                                   │
│  Hill-climbing on novelty_strength + bias_strength                      │
│  Signals: entropy + repetition + ambiguity load                         │
│  Writes: data/evolved_params.json                                       │
│                                                                         │
│  [ecology.py] CognitiveEcology                                          │
│  Orchestration heartbeat — sequences all 13 subsystems per tick         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  LLM LAYER — Runner page (manual / interactive)                         │
│                                                                         │
│  [modulator.py] Modulation Layer                                        │
│  Reads ambiguity score → selects prompt regime:                         │
│   low    → clean direct prompt                                          │
│   medium → injects top-5 graph neighbours as context                   │
│   high   → tension framing + 8 neighbours + meta-state pressure        │
│                                                                         │
│  Ollama (localhost:11434)                                               │
│  Models: qwen2.5:3b-instruct · qwen2.5:7b-instruct · llama3.2:3b      │
│  Default: qwen2.5:3b-instruct  (config.yaml)                           │
│                                                                         │
│  Step ⑤ — response fed back into engine:                               │
│   extract concepts from LLM answer → graph update → memory →           │
│   episodes → contradiction → world_model → ecology                     │
│   (engine learns from its own answers)                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Node Tree — Call Graph

```
AutoLearner.start()
│
├─── every 10s cycle ──────────────────────────────────────────────────────
│    │
│    ├── sources.*  (search + fetch)
│    │
│    ├── extractor.extract(sentence)
│    │       └── spaCy.load('en_core_web_sm')
│    │       └── SentenceTransformer.encode()     ← 384-dim MiniLM
│    │
│    ├── detector.detect_and_log(sentence, concepts, graph)
│    │       ├── TensionTracker.push()
│    │       └── NoveltyTracker.observe()
│    │
│    ├── SemanticGraph.update(concepts)
│    │       └── SQLite write (graph.db)
│    │
│    ├── MetaState.reinforce(concepts)
│    │       └── TensionTracker  (read)
│    │
│    ├── TemporalMemory.reinforce(concepts)
│    │
│    ├── ContradictionRegistry.observe(concepts)
│    │       ├── TensionTracker.push()
│    │       └── EpisodeStore.record()
│    │
│    ├── WorldModel.infer_from_context(concepts)
│    │       └── EpisodeStore  (read transitions)
│    │
│    ├── CognitiveEcology.tick(concepts)
│    │       ├── EnergyBudget.spend()
│    │       ├── MetaLearner.tick()
│    │       │       └── Evolver  (read)
│    │       ├── Evolver.tick()
│    │       │       └── ReflectionMonitor  (read)
│    │       ├── SelfModel.tick()
│    │       │       └── StabilityMonitor  (read)
│    │       ├── IdentityTracker.observe()
│    │       │       ├── NoveltyTracker  (read)
│    │       │       ├── MetaState  (read)
│    │       │       ├── StabilityMonitor  (read)
│    │       │       ├── Abstractor  (read)
│    │       │       └── ContradictionRegistry  (read)
│    │       ├── GoalEngine.tick()
│    │       │       ├── TensionTracker  (read)
│    │       │       ├── NoveltyTracker  (read)
│    │       │       ├── TemporalMemory  (read)
│    │       │       ├── ContradictionRegistry  (read)
│    │       │       ├── StabilityMonitor  (read)
│    │       │       └── MetaState  (read)
│    │       └── ReflectionMonitor.report()
│    │               ├── StabilityMonitor  (read)
│    │               ├── MetaState  (read)
│    │               ├── NoveltyTracker  (read)
│    │               ├── TemporalMemory  (read)
│    │               ├── TensionTracker  (read)
│    │               ├── ContradictionRegistry  (read)
│    │               └── GoalEngine  (read)
│    │
│    ├── EpisodeStore.record(concepts)
│    ├── Predictor.pre_activate(concepts, memory)
│    ├── StabilityMonitor.tick(MetaState)
│    ├── GoalEngine.tick()
│    ├── ReflectionMonitor.report()
│    ├── TemporalMemory.decay_to()
│    ├── EnergyBudget.replenish()
│    ├── SelfModel.tick()
│    ├── IdentityTracker.observe()
│    ├── MetaLearner.tick()
│    ├── Evolver.tick()
│    ├── NoveltyTracker.snapshot_top5(MetaState)
│    └── MetaState.decay_to()
│
└─── every 3 hours ────────────────────────────────────────────────────────
         ├── Abstractor.run()
         │       ├── EpisodeStore.cooccurrence_matrix()
         │       └── TemporalMemory  (read)
         │
         └── Worldview.update()
                 ├── TemporalMemory · ContradictionRegistry · Abstractor
                 ├── GoalEngine · StabilityMonitor · RegionIndex
                 └── EpisodeStore

Runner page (manual trigger)
│
├── extractor.extract(user_input)
├── detector.detect_and_log()
├── modulator.build_prompt()          ← graph neighbours + MetaState pressure
├── call_llm() → Ollama → Qwen/Llama
└── ⑤ extract(response) → full pipeline feedback loop
        └── graph · memory · episodes · contradiction · world_model · ecology
```

---

## Module Reference

| Module | Class | Purpose | Data File |
|---|---|---|---|
| learner.py | AutoLearner | Main loop — search, fetch, orchestrate | live_feed.jsonl, learner_stats.json, fetch_status.json, growth_log.jsonl |
| sources.py | — | Multi-source search + fetch | — (external APIs) |
| extractor.py | Concept | spaCy NLP + MiniLM embeddings | — (in-memory cache) |
| detector.py | AmbiguityResult | 3-metric ambiguity scoring | logs/ambiguity_scores.jsonl |
| modulator.py | ModulationResult | Graph-aware prompt builder + LLM call | logs/ab_log.jsonl |
| graph.py | SemanticGraph | NetworkX + SQLite knowledge store | graph.db, snapshots/*.json |
| episodes.py | EpisodeStore | Episode log + transition graph | episodes.jsonl, transitions.json |
| predictor.py | Predictor | Anticipatory pre-activation | — |
| abstractor.py | Abstractor | Co-occurrence → abstract concepts L0/L1/L2 | abstractions.json |
| meta_state.py | MetaState | 500-concept working attention pool | meta_state.json |
| memory.py | TemporalMemory | Three-layer temporal store | memory.json |
| contradiction.py | ContradictionRegistry | Bidirectional conflict detection | contradictions.json |
| world_model.py | WorldModel | Directed causal edge registry | world_model.json |
| novelty.py | NoveltyTracker | Exposure count + anti-loop detection | novelty.json |
| tension.py | TensionTracker | Cross-cutting conflict/ambiguity pressure | — (in-memory) |
| stability.py | StabilityMonitor | Shannon entropy → 5 cognitive modes | — |
| goals.py | GoalEngine | 5 competing intrinsic drives | — |
| energy.py | EnergyBudget | Finite energy pool per activity | energy.json |
| self_model.py | SelfModel | Recursive self-prediction accuracy | self_model.json |
| identity.py | IdentityTracker | 5 slowly-drifting personality traits | identity.json |
| worldview.py | Worldview | Longitudinal identity (5 dimensions) | worldview.json |
| reflection.py | ReflectionMonitor | Unified self-report + pathology detection | reflection.json |
| meta_learning.py | MetaLearner | Strategy scoring via prediction accuracy | meta_learning.json |
| evolver.py | Evolver | Hill-climbing parameter adaptation | evolved_params.json |
| ecology.py | CognitiveEcology | Orchestration heartbeat (13 subsystems) | — |

---

## Data Files

```
data/
├── graph.db               ← SemanticGraph  (SQLite — nodes + edges)
├── episodes.jsonl         ← EpisodeStore   (concept co-occurrence log)
├── transitions.json       ← EpisodeStore   (directed transition weights)
├── live_feed.jsonl        ← AutoLearner    (last 200 activity entries)
├── learner_stats.json     ← AutoLearner    (total_sentences, total_concepts)
├── fetch_status.json      ← AutoLearner    (fetching bool + started_at)
├── growth_log.jsonl       ← AutoLearner    (node/edge count per cycle)
├── paused.txt             ← AutoLearner    ("1" paused / "0" running)
├── engine_config.json     ← Config         (all params + ego presets)
├── meta_state.json        ← MetaState      (activation pool snapshot)
├── memory.json            ← TemporalMemory (3-layer memory snapshot)
├── contradictions.json    ← ContradictionRegistry
├── world_model.json       ← WorldModel     (causal edge registry)
├── novelty.json           ← NoveltyTracker (exposure counts)
├── energy.json            ← EnergyBudget   (current pool level)
├── self_model.json        ← SelfModel      (prediction accuracy history)
├── identity.json          ← IdentityTracker (5 personality traits)
├── worldview.json         ← Worldview      (longitudinal identity)
├── reflection.json        ← ReflectionMonitor (last self-report)
├── meta_learning.json     ← MetaLearner    (strategy scores)
├── evolved_params.json    ← Evolver        (adapted parameters)
└── abstractions.json      ← Abstractor     (abstract concept hierarchy)

snapshots/
└── *.json                 ← SemanticGraph  (daily node+edge snapshots)

logs/
├── ambiguity_scores.jsonl ← detector.py   (per-sentence ambiguity log)
└── ab_log.jsonl           ← modulator.py  (A/B LLM response log)
```

---

## LLM Integration (Ollama)

```
config.yaml
  model:
    name:     qwen2.5:3b-instruct
    endpoint: http://localhost:11434/api/generate
    stream:   false

Available models (installed):
  qwen2.5:3b-instruct    ← default, fast
  qwen2.5:7b-instruct    ← smarter, slower
  llama3.2:3b            ← alternative

Modulation regimes:
  low    ambiguity → bare system prompt
  medium ambiguity → + top-5 graph neighbours injected
  high   ambiguity → + tension framing + 8 neighbours + meta-state pressure
```

---

## Ego System (Settings page)

```
Max 3 named personality presets stored in engine_config.json under "egos"
Each ego captures: identity · evolver · meta_state · goals · attention

engine_config.json structure:
{
  "active_ego": "Curious",
  "egos": {
    "Curious":  { "identity": {...}, "evolver": {...}, "meta_state": {...}, ... },
    "Stable":   { ... },
    "Wild":     { ... }
  },
  "learning": { ... },
  ...
}

UI: dropdown → Load / Delete · text input → Save Ego
Reset to defaults preserves egos.
```

---

## Graph Visualisation

```
Layout: UMAP (umap-learn)
  Input:  384-dim MiniLM embeddings per node
  Output: 2D coordinates — semantic proximity = spatial proximity
  Params: n_neighbors=15, min_dist=0.1, metric=cosine
  Cache:  st.cache_data keyed on node ID tuple

Colour coding:
  Blue   (#4a9eff) — Left-brain  (logic, math, science, structure)
  Red    (#ff6b6b) — Right-brain (emotion, art, music, metaphor)
  Orange (#f5a623) — Bridge      (both hemispheres)
  Grey   (#555566) — Uncategorised
```

---

## Scaling Projections

```
Safe zone     <50k nodes    — <1 GB RAM, everything fast
Manageable    50–120k nodes — 1–3 GB RAM, startup slows
Danger zone   >150k nodes   — RAM pressure, prune aggressively
Hard limit    ~300k nodes   — process OOM risk

Bottlenecks:
  1. NetworkX full graph in RAM         → use SQLite-only queries where possible
  2. episodes.jsonl unbounded           → add rolling 30-day window
  3. Embedding cache unbounded dict     → cap at 50k entries with LRU
  4. SQLite cold load at 100k+ nodes    → 3–5s startup penalty
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

## Goal Drives

| Drive | Default Weight | Signal Source |
|---|---|---|
| reduce_uncertainty | 0.30 | TensionTracker + ContradictionRegistry |
| increase_novelty | 0.25 | NoveltyTracker |
| resolve_contradiction | 0.20 | ContradictionRegistry |
| maintain_stability | 0.15 | StabilityMonitor entropy |
| expand_regions | 0.10 | RegionIndex coverage |

## Memory Layers

| Layer | Decay Rate | Promotion |
|---|---|---|
| working | 0.97 | → episodic on repeated reinforcement |
| episodic | 0.9997 | → semantic on sustained activation |
| semantic | 0.99997 | permanent long-term store |

## Dashboard Metrics

| Metric | Source | Meaning |
|---|---|---|
| Nodes | SQLite COUNT(*) | Total concepts in graph |
| Edges | SQLite COUNT(*) | Total co-occurrence links |
| Sentences | learner_stats.json | Total sentences processed |

---

*Updated 2026-05-24*
