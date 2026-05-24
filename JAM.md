# JAM Field Core — Architecture

**JAM** = Joint Activation Map

A unified cognitive substrate that replaces 21 scattered subsystem modules
with three tightly coupled layers. Every concept node in the engine carries
10 live field properties. No fixed symbolic memory — an evolving field state.

---

## Why JAM Replaced the Old Architecture

The previous system had 21 independent modules, each with its own:
- In-memory data structure
- Background decay thread
- JSON snapshot file
- Singleton lifecycle

This caused: cascading import errors, race conditions across decay loops,
incoherent state across modules, and a 22-minute hang from
`ThreadPoolExecutor.shutdown(wait=True)` blocking on stuck workers.

**JAM collapses all of this into one field, one tick, one store.**

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  EXTERNAL INPUT                                                 │
│  Wikipedia · arXiv · Gutenberg · Reddit · OpenAlex · Web       │
└────────────────────────────┬────────────────────────────────────┘
                             │ articles (sentences + embeddings)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Ingestion (src/learner.py)                          │
│  AutoLearner: 10s cycle · 8 workers · 12 topics/cycle         │
│  fetch → embed → graph.update() → JAM field ingest            │
│  Threads: learner · subsys · regulator · watchdog             │
└────────────────────────────┬────────────────────────────────────┘
                             │ concepts + node_ids per article
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 — JAM Field (src/jam_field.py)             [NEW]      │
│  Unified 10-property node store                                │
│  SQLite table jam_field in graph.db (shared with graph)        │
│  In-memory dict for fast reads; flushed every 100 updates      │
│                                                                 │
│  Per-node properties:                                          │
│    activation   current firing strength      [0, 1]           │
│    ambiguity    local neighbourhood entropy  [0, 1]           │
│    momentum     directional carry            [0, 1]           │
│    stability    consistency of activation    [0, 1]           │
│    resonance    alignment with field centroid [0, 1]          │
│    persistence  durable exposure saturation  [0, 1]           │
│    novelty      freshness (inverse log)      [0, 1]           │
│    tension      distance from field centroid [0, 1]           │
│    coherence    local cluster density        [0, 1]           │
│    drift        rate of activation change    [0, 1]           │
└────────────────────────────┬────────────────────────────────────┘
                             │ active nodes + graph topology
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3 — Field Dynamics (src/field_dynamics.py)   [NEW]      │
│  Runs once per cycle (post-ingest). Non-blocking lock.         │
│                                                                 │
│  Computes:                                                     │
│    centroid      activation-weighted mean embedding            │
│    tension       cosine distance from centroid per node        │
│    resonance     1 - tension (centroid alignment)             │
│    ambiguity     std-dev of neighbour activations              │
│    coherence     fraction of graph neighbours that are active  │
│    propagation   activation bleeds to top-k graph neighbours   │
└────────────────────────────┬────────────────────────────────────┘
                             │ field snapshot every 60s
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 4 — Regulation (src/regulation.py)           [NEW]      │
│  Single 60s tick. Replaces 9 old subsystems.                  │
│                                                                 │
│  Reads: field.snapshot() → active_count, coherence, entropy,  │
│         mean_tension, mean_stability, mean_novelty, saturation │
│                                                                 │
│  Elects mode + goal from field metrics                         │
│  Adjusts 3 global scalars (exponential blend α = 0.15)        │
│                                                                 │
│  Writes: data/regulation.json, data/cog_status.json           │
└────────────────────────────┬────────────────────────────────────┘
                             │ mode/goal/scalars
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 5 — Subsystems (background thread, 30s)                 │
│  contradiction.py · world_model.py · episodes.py · predictor  │
│  These operate on concept text lists, not the field directly   │
└────────────────────────────┬────────────────────────────────────┘
                             │ long-term structure
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 6 — Identity (every 3h)                                 │
│  abstractor.py · worldview.py                                  │
│  Cluster → abstract nodes · longitudinal identity snapshot    │
│  worldview reads JAM field persistence for "semantic memory"   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Reference

### `src/jam_field.py` — Layer 2

**Purpose**: Unified in-memory + SQLite field store for all 10 node properties.

**Replaces**: meta_state.py, memory.py, tension.py, novelty.py (concept storage)

**Storage**: `jam_field` table in `data/graph.db` (shared with SemanticGraph)

**SQLite schema**:
```sql
CREATE TABLE jam_field (
    text        TEXT PRIMARY KEY,
    node_id     TEXT,
    activation  REAL DEFAULT 0.0,
    ambiguity   REAL DEFAULT 0.0,
    momentum    REAL DEFAULT 0.0,
    stability   REAL DEFAULT 0.5,
    resonance   REAL DEFAULT 0.0,
    persistence REAL DEFAULT 0.0,
    novelty     REAL DEFAULT 1.0,
    tension     REAL DEFAULT 0.0,
    coherence   REAL DEFAULT 0.0,
    drift       REAL DEFAULT 0.0,
    times_seen  INTEGER DEFAULT 1,
    first_seen  REAL NOT NULL,
    updated_at  REAL NOT NULL
)
```

**Key constants**:
| Constant | Value | Meaning |
|---|---|---|
| `_GAIN` | 0.30 | Activation gain per observation (sigmoid saturation) |
| `_DECAY_RATE` | 0.9972 | Per-minute activation decay (~0.85 after 60 min) |
| `_MIN_ACTIVE` | 0.001 | Prune threshold |
| `_FLUSH_EVERY` | 100 | SQLite write batch size |
| `_MAX_NODES` | 20,000 | In-memory cap (oldest pruned) |

**Core formula (ingest)**:
```
prev_act   = stored_act × decay_rate ^ elapsed_minutes   (lazy decay)
new_act    = prev_act + GAIN × (1 - prev_act)            (sigmoid saturation)
momentum   = prev_act × 0.65 + old_momentum × 0.35
drift      = |new_act - prev_act|
stability  = old_stability × 0.92 + (1 - min(drift×8, 1)) × 0.08
novelty    = 1 / (1 + log(1 + times_seen))
persistence = 1 - exp(-times_seen / 25)
```

**Public API**:
```python
JamField.get()                       # singleton
field.ingest(concepts, node_ids)     # update from one article batch
field.update_dynamics(props)         # write ambiguity/resonance/tension/coherence
field.decay()                        # time-decay all nodes, prune floor, snapshot
field.top(n, by='activation')        # list[(text, props_dict)]
field.active(threshold=0.05)         # {text: live_activation}
field.snapshot()                     # summary dict → data/jam_field.json
field.flush()                        # force SQLite write
```

---

### `src/field_dynamics.py` — Layer 3

**Purpose**: Computes emergent properties that require global field context + graph topology.

**Runs**: Once per learner cycle, after all articles in the batch are ingested.

**Non-blocking**: Skips if a previous pass is still running (`lock.acquire(blocking=False)`).

**Key constants**:
| Constant | Value | Meaning |
|---|---|---|
| `_DIFFUSION` | 0.12 | Fraction of activation that bleeds to neighbours |
| `_ACT_THRESHOLD` | 0.05 | Minimum activation to participate in propagation |
| `_TOP_ACTIVE` | 200 | Max nodes considered active for centroid/resonance |
| `_PROPAGATE_K` | 6 | Top-k neighbours to propagate to per node |

**Computation pipeline**:
```
1. field.active(0.05)           → active nodes dict
2. fetch top-200 by activation  → pull embeddings from graph
3. _compute_centroid()          → activation-weighted mean embedding (L2 normalised)
4. per node:
     tension   = cosine_distance(embedding, centroid)
     resonance = 1 - tension
     ambiguity = std_dev(neighbour activations)
     coherence = active_neighbours / total_neighbours
5. _compute_propagation()       → delta = DIFFUSION × act × min(edge_weight, 1.0)
                                   for top-k neighbours by edge weight
6. _apply_propagation()         → field node += delta × 0.3  (capped at 1.0)
7. field.update_dynamics(props) → write back all per-node dynamics
```

**Public API**:
```python
FieldDynamics.get()
dynamics.propagate(field, graph)   # one full pass
```

---

### `src/regulation.py` — Layer 4

**Purpose**: Single 60-second regulation tick. Reads field state → elects mode/goal → adjusts 3 global scalars.

**Replaces**: MetaRegulator, StabilityMonitor, GoalEngine, EnergyBudget, MetaLearner, Evolver, SelfModel, IdentityTracker, ReflectionMonitor (9 modules)

**Regulated scalars**:
| Scalar | Bounds | Default | Meaning |
|---|---|---|---|
| `gain_rate` | [0.10, 0.50] | 0.30 | Activation gain per observation |
| `decay_rate` | [0.990, 0.999] | 0.9972 | Per-minute activation decay |
| `diffusion_strength` | [0.05, 0.25] | 0.12 | Fraction propagated to neighbours |

**Scalar adjustment** (exponential blend, α = 0.15):
```
target_gain      = DEFAULT_GAIN × (1 - pressure×0.4 + entropy×0.2)
target_decay     = DEFAULT_DECAY - (pressure - 0.5) × 0.004
target_diffusion = DEFAULT_DIFFUSION × (0.5 + entropy_norm)

new_scalar = old × (1 - α) + target × α   [clamped to bounds]
```

**Mode election** (from field metrics):
| Mode | Condition |
|---|---|
| `focused` | coherence > 0.40 |
| `conflicted` | mean_tension > 0.55 |
| `saturated` | saturation > 0.70 |
| `exploratory` | entropy_norm > 0.75 |
| `drifting` | mean_stability < 0.35 |
| `associative` | default |

**Goal election**:
| Goal | Condition |
|---|---|
| `resolve_tension` | mean_tension > 0.50 |
| `expand_knowledge` | coherence < 0.10 |
| `consolidate` | coherence > 0.45 and mean_novelty < 0.30 |
| `stabilise` | entropy_norm > 0.70 |
| `explore` | default |

**Entropy computation**:
```
top-20 nodes by activation → normalize to probability distribution
entropy = -Σ p×log2(p)
entropy_norm = entropy / log2(max(len(probs), 2))   → [0, 1]
```

**Field pressure**:
```
saturation = min(active_count / field_size × 20, 1.0)
pressure   = saturation×0.3 + mean_tension×0.4 + entropy_norm×0.3
```

**Outputs**:
- `data/regulation.json` — mode, goal, scalars, entropy, pressure, tick_count
- `data/cog_status.json` — {mode, goal} (overlay + sidebar compatibility)

**Public API**:
```python
Regulation.get()
regulation.tick(field)    # one regulation pass
regulation.snapshot()     # current state dict
regulation.mode           # str property
regulation.goal           # str property
```

---

## Data Files Written by JAM

| File | Written by | Contains |
|---|---|---|
| `data/graph.db` (table `jam_field`) | JamField | All 10 properties per concept, SQLite |
| `data/jam_field.json` | JamField.decay() | field_size, active_count, coherence, top-5, field_stats |
| `data/regulation.json` | Regulation.tick() | mode, goal, scalars, entropy, pressure, tick_count |
| `data/cog_status.json` | Regulation.tick() | {mode, goal} — for overlay + sidebar chip |

---

## Thread Model

```
learner thread  (10s cycle)
  └─ _cycle()
       ├─ search phase   → ThreadPoolExecutor(8), shutdown(wait=False)
       ├─ process phase  → ThreadPoolExecutor(8), shutdown(wait=False)
       │    └─ per article: fetch → embed → graph.update → field.ingest
       ├─ dynamics.propagate(field, graph)   [Layer 3]
       └─ field.decay()                      [prune + snapshot]

subsys thread  (30s)
  └─ contradiction.observe()
     world_model.infer_from_context()
     episodes.record()
     predictor.pre_activate()

regulator thread  (60s, starts after 60s delay)
  └─ Regulation.tick(field)                  [Layer 4]

watchdog thread  (60s poll, starts after 120s delay)
  └─ if learner thread dead → restart it
```

**ThreadPoolExecutor safety**: Both search and process pools use explicit
`shutdown(wait=False, cancel_futures=True)` in `finally` blocks — never
`with ThreadPoolExecutor() as pool:` which blocks on `__exit__` even when
workers are stuck.

---

## What Was Deleted (21 modules)

| Module | Replaced by |
|---|---|
| `meta_state.py` | JAM field activation + top() query |
| `memory.py` | JAM field activation (working), momentum (episodic), persistence (semantic) |
| `tension.py` | JAM field tension property (per-node cosine distance from centroid) |
| `novelty.py` | JAM field novelty property (1 / log(1 + times_seen)) |
| `stability.py` | Regulation mode election |
| `goals.py` | Regulation goal election |
| `energy.py` | Regulation pressure scalar |
| `self_model.py` | Regulation tick_count + scalar tracking |
| `identity.py` | Regulation mode history |
| `reflection.py` | Regulation snapshot |
| `meta_learning.py` | Regulation scalar adjustment |
| `evolver.py` | Regulation exponential blend |
| `ecology.py` | Learner subsys_worker (simplified) |
| `regulator.py` | regulation.py (complete rewrite) |
| `agents.py` | Unused |
| `api.py` | Unused |
| `attention.py` | Unused |
| `active_inference.py` | Unused |
| `feed.py` | Unused |
| `sunday.py` | Unused |
| `queue_mgr.py` | Unused |

---

## UI Mapping

| Page | Old source | New source |
|---|---|---|
| Core (1_state.py) — coherence | MetaState.snapshot() | jam_field.json coherence |
| Field State (0_meta_state.py) | MetaState activation pool | jam_field.json top + regulation.json |
| Cognition Live tab | ReflectionMonitor.report(), GoalEngine, StabilityMonitor | regulation.json + jam_field.json |
| Cognition Memory tab | TemporalMemory 3-layer snapshot | JAM field top(n, by='activation/momentum/persistence') |
| Cognition Predictions tab | TemporalMemory.top_working() as seeds | JamField.top(10, by='activation') |
| Cognition Simulate tab | TemporalMemory.top_working() as seeds | JamField.top(5, by='activation') |
| Runner (3_runner.py) | MetaState.get() → build_prompt(meta=...) | build_prompt(meta=None) |
| Sidebar chip | GoalEngine.current_goal(), StabilityMonitor._current_mode | cog_status.json |
| Worldview — persistent concepts | TemporalMemory._semantic layer | JamField.top(50, by='persistence') |
| Worldview — semantic biases | TemporalMemory._semantic weights | JamField persistence weights |
| Worldview — goal/mode history | GoalEngine + StabilityMonitor | cog_status.json |

---

## Graph Database Layout

Single SQLite file `data/graph.db` shared by two systems:

```
graph.db
├── nodes       (SemanticGraph — concept nodes with embeddings)
├── edges       (SemanticGraph — semantic similarity edges)
└── jam_field   (JamField — 10 live field properties per concept)
```

As of 2026-05-25:
- `nodes`: 24,806 rows
- `edges`: 111,962 rows (post-maintenance pruning)
- `jam_field`: 0 rows (populates on first learner cycle)

Both tables use `WAL` journal mode + `NORMAL` synchronous for concurrent reads.

---

## Property Lifecycle

```
INGEST (per article, every 10s cycle)
  ├── activation:   lazy-decayed then sigmoid gain
  ├── momentum:     weighted carry of previous activation
  ├── drift:        |new_act - prev_act|
  ├── stability:    EMA of (1 - drift)
  ├── novelty:      1 / (1 + log(1 + times_seen))
  └── persistence:  1 - exp(-times_seen / 25)

DYNAMICS (once per cycle, post-ingest)
  ├── tension:      cosine_distance(embedding, field_centroid)
  ├── resonance:    1 - tension
  ├── ambiguity:    std_dev(neighbour_activations)
  └── coherence:    active_neighbours / total_neighbours

DECAY (once per cycle)
  ├── activation:   × decay_rate ^ elapsed_minutes
  ├── momentum:     × 0.80
  └── prune if activation < 0.001

REGULATION (every 60s)
  ├── reads: coherence, tension, entropy, stability, novelty, saturation
  ├── elects: mode (6 states) + goal (5 states)
  └── adjusts: gain_rate, decay_rate, diffusion_strength
```

---

## Activation Half-Lives

With default `decay_rate = 0.9972` per minute:

| Time | Remaining activation |
|---|---|
| 10 min | 97.2% |
| 30 min | 91.9% |
| 1 hour | 84.5% |
| 3 hours | 60.3% |
| 6 hours | 36.4% |
| 12 hours | 13.2% |
| 24 hours | 1.7% |

Nodes are pruned when activation drops below 0.001 (≈48h at default rate).

The regulator can lower `decay_rate` toward 0.990 under high pressure
(faster cleanup) or raise toward 0.999 under sparse conditions (longer memory).

---

## Cognitive Modes vs Field State

| Mode | Field condition | Behavioural effect |
|---|---|---|
| `focused` | coherence > 0.40 | low diffusion, high gain — deepen current cluster |
| `conflicted` | mean_tension > 0.55 | goal = resolve_tension — seek bridging concepts |
| `saturated` | saturation > 0.70 | faster decay, lower gain — prune overcrowded field |
| `exploratory` | entropy > 0.75 | high diffusion — spread activation to new areas |
| `drifting` | stability < 0.35 | goal = stabilise — reduce noise, seek anchors |
| `associative` | default | balanced — normal operation |

---

*Generated 2026-05-25 · JAM Field Core v1.0*
