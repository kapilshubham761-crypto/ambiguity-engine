# Ambiguity Engine — Architecture Reference
*V9 · 2026-05-27*

---

## Table of Contents

1. [Philosophy](#1-philosophy)
2. [System Overview](#2-system-overview)
3. [Layer 1 — Discovery & Encoding](#3-layer-1--discovery--encoding)
   - [Sources](#31-sources)
   - [AutoLearner](#32-autolearner)
   - [Extractor](#33-extractor)
   - [EmbeddingIndex](#34-embeddingindex)
4. [Layer 2 — Ambiguity Detection & Modulation](#4-layer-2--ambiguity-detection--modulation)
   - [Detector](#41-detector)
   - [Modulator](#42-modulator)
5. [Layer 3 — Semantic Graph & Field State](#5-layer-3--semantic-graph--field-state)
   - [JAM Field](#51-jam-field)
   - [Field Dynamics](#52-field-dynamics)
6. [Layer 4 — Higher-Order Cognition](#6-layer-4--higher-order-cognition)
7. [Layer 5 — Self-Regulation](#7-layer-5--self-regulation)
8. [Multimodal Pipeline](#8-multimodal-pipeline)
9. [HTTP Server](#9-http-server)
10. [Data Files](#10-data-files)
11. [config.yaml Reference](#11-configyaml-reference)
12. [Key Architectural Decisions](#12-key-architectural-decisions)
13. [Launching](#13-launching)

---

## 1. Philosophy

Ambiguity is not noise to filter out — it is the primary cognitive signal. Concepts that pull in multiple directions are attractors that drive exploration, tension resolution, and learning. This system builds a living semantic field that grows richer through contradiction, not despite it.

*Intelligence as dynamic ambiguity management: not compression toward certainty, but proliferation and curation of multiple incompatible models held in productive tension until resolution emerges.*

---

## 2. System Overview

A fully autonomous, local AI that teaches itself 24/7 by fetching real internet content, extracting semantic concepts, and evolving a knowledge graph. No human review. No cloud AI. No paid APIs.

```
INTERNET SOURCES
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 1 — DISCOVERY & ENCODING                     │
│  Sources → AutoLearner → Extractor                  │
│  (10s cycle · 256 workers · 384-dim MiniLM)         │
│  GPU: ONNX Runtime CUDA on RTX 3050 (8150 sent/s)  │
└────────────────────────┬────────────────────────────┘
                         │ Concept[]
                         ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 2 — AMBIGUITY DETECTION & MODULATION         │
│  Detector (variance + cluster + bridge)             │
│  → AmbiguityResult → Modulator → LLM system prompt │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 3 — SEMANTIC GRAPH & FIELD STATE             │
│  EmbeddingIndex (in-memory k-NN, numpy)             │
│  JAM Field (10 properties per node)                 │
│  Field Dynamics (global context pass)               │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 4 — HIGHER-ORDER COGNITION                   │
│  Episodes → Predictor → Simulator                   │
│  Contradictions → World Model → Abstractor          │
│  Worldview (identity portrait)                      │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  LAYER 5 — SELF-REGULATION                          │
│  Regulation (60s tick · mode + goal + 3 scalars)    │
└─────────────────────────────────────────────────────┘
```

**Runtime threads:** Learner (10s) · Subsystems (30s) · Regulation (60s) · Watchdog

---

## 3. Layer 1 — Discovery & Encoding

### 3.1 Sources

`src/sources.py` — Multi-source content discovery using only free APIs.

**Text sources:**

| Source | Weight | What it fetches |
|---|---|---|
| `wikipedia` | 2.5 | English Wikipedia REST API |
| `web` | 2.5 | DuckDuckGo — open internet |
| `arxiv` | 2.0 | Academic paper abstracts |
| `openalex` | 2.0 | Open academic literature |
| `semanticscholar` | 2.0 | Semantic Scholar papers |
| `wikidata` | 2.0 | Wikidata structured knowledge |
| `conceptnet` | 2.0 | ConceptNet commonsense relations |
| `reddit` | 1.8 | Public posts via Reddit JSON API |
| `hackernews` | 1.8 | Hacker News posts |
| `pubmed` | 1.8 | Biomedical literature |
| `crossref` | 1.5 | DOI-indexed research |
| `newsrss` | 1.5 | RSS news feeds |
| `stackexchange` | 1.5 | Stack Exchange Q&A |
| `wordnet` | 1.5 | WordNet synsets and definitions |
| `devto` | 1.2 | Dev.to articles |
| `simplewiki` | 1.0 | Simple English Wikipedia |
| `gutenberg` | 1.0 | Public domain books (Gutendex API) |
| `internetarchive` | 0.8 | Internet Archive texts |
| `image` | 3.5 | Images — MiniLM-embedded via title text |
| `audio` | 5.0 | Audio — highest priority; MiniLM-embedded via title text |

Weights can be overridden live via `engine_config.json → source_weights`. UMBER applies goal-based multipliers each cycle so effective weights shift dynamically.

**Priority order:** audio (50%) > image (34%) > text (16%) — all three run in parallel each cycle.

**Image sources** (fallback to DuckDuckGo if no API key):

| Source | Auth |
|---|---|
| Unsplash | API key (`image_apis.unsplash`) |
| Pexels | API key (`image_apis.pexels`) |
| Pixabay | API key (`image_apis.pixabay`) |
| Flickr | API key (`image_apis.flickr`) |
| ArtStation | Public JSON API (no key) |
| Instagram | Username + password via instagrapi · session cached to `data/instagram_session.json` |
| Behance | API key (`image_apis.behance`) |
| DeviantArt | Client ID + Secret |
| Bing Images | HTML scrape (`mediaurl=` regex) |

**Audio sources:** Wikimedia Commons (no key required)

**Key details:**
- User-agent: `AmbiguityEngine/0.1 (research toy; contact: local)`
- Sentence splitter: regex on punctuation + capital/digit; filters [40–400 chars], ≥4 words
- Code-content filter: blocks GitHub, Stack Overflow, programming tutorials
- Each topic picks 3 sources per cycle via weighted `random.choices`
- All image/audio searchers run in parallel via `ThreadPoolExecutor`; 12s timeout; URL deduplication

---

### 3.2 AutoLearner

`src/learner.py` — Main orchestrator. Runs 4 background threads.

**Threads:**

| Thread | Interval | Responsibility |
|---|---|---|
| Learner | 10s cycle | Fetch → extract → graph → field → dynamics → decay |
| Subsystems | 30s | Contradiction, world model, episodes, predictor pre-activation |
| Regulation | 60s | `Regulation.tick()` — adjusts gain/decay/diffusion |
| Watchdog | continuous | Restart learner thread if dead |

**10-second cycle breakdown:**

```
1. Search phase (timeout: 2 × search_timeout)
   - Sample 200 topics from TOPICS list
   - Search each topic in parallel (256 workers), 3 sources per topic
   - Split results: text_items + mm_items (image/audio)
   - Skip known URLs (rolling 50K-URL window)
   - Skip code content

2. Fetch phase — two-stage overlapped IO/CPU pipeline
   - Stage A: HTTP download (IO pool, N_WORKERS_FETCH workers)
   - Stage B: HTML parse via trafilatura (CPU pool, runs as each download completes)
   - Dedup sentences across articles before encoding

3. Encode + Ingest (background worker — non-blocking for cycle)
   - extract_batch(all_sents) → Concept[] via EmbeddingIndex + MiniLM/ORT
   - EmbeddingIndex.update(concepts) → activates k-NN rebuild threshold
   - JamField.ingest(concepts, node_ids)
   - detect_and_log() → ambiguity score
   - Feed sentence texts to subsystem queue
   - Multimodal items: _process_multimodal() → kNN retrieval → JAM activation

4. Checkpoints (every 3 hours)
   - Abstractor.run()
   - Worldview.update()
```

**Multimodal handling (V8):**
- Image/audio items separated from text pipeline
- `_process_multimodal()`: embeds media → stores in EmbeddingIndex → `knn(vec, k=12)` finds nearest text concepts → creates synthetic `Concept` objects → `JamField.ingest()` activates them
- The image acts as a retrieval key that triggers semantically related concepts in JAM — no direct injection of pixel-space vectors into the semantic graph

**Key constants:**

| Constant | Value |
|---|---|
| CYCLE_TIME | 0s (no sleep between cycles) |
| N_WORKERS | 256 search + 256 fetch |
| N_ENCODER_WORKERS | 16 parallel encoder threads |
| TOPICS_PER_CYCLE | 200 |
| MAX_RESULTS_PER | 30 results per topic per source |
| FETCH_TIMEOUT | 8s |
| SEARCH_TIMEOUT | 5s |
| FEED_MAX | 500 rolling entries |
| MIN_SENTENCES | 2 per article |
| MAX_SENTENCES | 8 per article |

**Flattened search (V9):** 200 topics × 5 sources = up to 1000 independent futures submitted simultaneously to `_search_pool`. Audio+image slots guaranteed per topic; remaining slots use weighted random text source selection. Cache sizes: URL dedup 50K, sentence dedup 50K, search cache 20K.

**Persistence:**

| File | Contents |
|---|---|
| `data/learner_stats.json` | `{total_concepts, total_sentences}` |
| `data/live_feed.jsonl` | Rolling 500-entry activity log |
| `data/fetch_status.json` | `{fetching, started_at}` |
| `data/currently_reading.json` | `{title, source, goal, ts}` — written at START of each fetch |
| `data/perf_profile.json` | Rolling 50-sample pipeline timing averages |
| `data/paused.txt` | `"1"` paused / `"0"` running |

---

### 3.3 Extractor

`src/extractor.py` — Converts text to 384-dim embeddings. **Import torch FIRST** to avoid circular import.

**Model:** `sentence-transformers/paraphrase-MiniLM-L3-v2` (384-dim)

**GPU backend selection (V8):**

| Backend | Speed | Condition |
|---|---|---|
| ORT CUDA (`_OrtCudaEncoder`) | ~8150 sent/sec on RTX 3050 | onnxruntime-gpu installed, CUDA DLLs found in PATH |
| ONNX CPU | ~2000 sent/sec | onnxruntime installed, no CUDA |
| PyTorch CPU (SentenceTransformer) | ~500 sent/sec | fallback |

**CUDA DLL resolution:** Nvidia pip packages install DLLs into `.venv/Lib/site-packages/nvidia/*/bin/`. At startup, extractor.py prepends all those bin dirs to `os.environ['PATH']` before any imports (Windows DLL loading uses PATH, not `os.add_dll_directory()`).

**`_OrtCudaEncoder`** — Direct ORT inference that bypasses `sentence_transformers` entirely. Uses ONNX model from HuggingFace Hub (`onnx/model_O3.onnx`), raw tokenizer, manual mean-pooling:
```python
hidden = session.run(None, feeds)[0]   # (batch, seq_len, 384)
mask_e = attention_mask[:, :, np.newaxis]
vecs   = (hidden * mask_e).sum(axis=1) / mask_e.sum(axis=1)
```

**Cache:**
1. In-memory LRU (`_embed_cache`, cap 500K sentences) — O(1) lookup
2. Encoder — one forward pass for all cache misses

**Public API:**
```python
Concept(text, embedding, source)   # NamedTuple
extract(text) → list[Concept]
extract_batch(sentences) → list[list[Concept]]
```

Limits: skips text <10 chars; truncates to 500 chars.

---

### 3.4 EmbeddingIndex

`src/embed_index.py` — In-memory k-NN store. Pure numpy, no NetworkX, no SQLite.

**Public API:**
```python
EmbeddingIndex.get()                                          # singleton
update(concepts) → list[str]                                  # add/update; triggers rebuild every 2000 new items
knn(embedding, k=8, exclude=None) → list[tuple[str, float]]  # (text, cosine_sim)
knn_text(text, k=8) → list[tuple[str, float]]                # k-NN by stored text key
get_embedding(text) → np.ndarray | None                       # stored embedding for a text key
node_count                                                    # property
```

**Rebuild strategy:** maintains a normalised committed matrix for BLAS-fast bulk search; brute-forces the small uncommitted tail between rebuilds (every 2000 new items).

**Persistence:** `data/embed_cache.npy` (float32 matrix) + `data/embed_cache.meta.json` (text list). Async save via background thread.

---

## 4. Layer 2 — Ambiguity Detection & Modulation

### 4.1 Detector

`src/detector.py` — Quantifies semantic ambiguity via three independent metrics.

**Public API:**
```python
detect(concepts, graph, weights) → AmbiguityResult
detect_and_log(text, concepts, graph) → AmbiguityResult   # + side effects
```

**Three metrics** (default weights: 0.45 + 0.45 + 0.10):

| Metric | What it measures | Implementation |
|---|---|---|
| Variance | How spread are the input embeddings? | Mean pairwise cosine distance (vectorized upper-triangle) |
| Cluster | How bimodal is the input? | k=2 KMeans centroid cosine distance |
| Bridge | Do inputs pull toward disconnected graph regions? | Cosine distance between neighbour centroids in graph |

**Score classification:**

| Range | Level |
|---|---|
| [0.0, 0.3) | low |
| [0.3, 0.6) | medium |
| [0.6, 1.0] | high |

**Edge cases:**
- 0 concepts → score 0.0, level "low"
- <3 concepts → score capped at 0.59 (never "high" from tiny input)

**Side effects in `detect_and_log`:**
- Feeds each concept's score to `TensionTracker.observe()`
- Records concept exposure in `NoveltyTracker.observe()`
- Appends to `logs/ambiguity_scores.jsonl`

---

### 4.2 Modulator

`src/modulator.py` — Translates ambiguity score + graph context into an LLM system prompt.

**Public API:**
```python
build_prompt(concepts, result, graph, meta=None) → ModulationResult
call_llm(user_input, system_prompt, cfg) → str
warmup_model(cfg)
run_ab(user_input, concepts, result, graph, cfg) → dict
```

**Three prompt regimes:**

| Ambiguity | Prompt style | Graph injection |
|---|---|---|
| low | Clear, direct assistant | None |
| medium | Thoughtful; memory-aware | Top 5 neighbours by edge weight |
| high | Tension framing; maps conceptual territory | Top 8 neighbours + meta-state pressure concepts |

**LLM call parameters:**
```python
{
  'model':       cfg['model']['name'],   # default: qwen2.5:1.5b
  'keep_alive':  -1,                     # never unload from VRAM
  'num_ctx':     1024,
  'num_predict': 200,
}
```

**Streaming SSE events** (`/api/runner/stream`):
```
event: meta   data: {"concepts": [...], "ambiguity": {...}, "pre_ms": 5}
event: token  data: "Hello"
...
event: done   data: {"total_ms": 3050, "llm_ms": 3045}
```
First token appears <300ms.

---

## 5. Layer 3 — Semantic Graph & Field State

### 5.1 JAM Field

`src/jam_field.py` — Living activation landscape. 10 properties per node, continuously decaying and updating.

**Public API:**
```python
JamField.get()                          # singleton
ingest(concepts, node_ids)              # update from article batch
update_dynamics(updates)                # write back from dynamics pass
decay()                                 # time-decay + prune floor
set_decay_rate(rate)                    # live override from Regulation
top(n, by='activation') → list
active(threshold=0.05) → {text: activation}
snapshot() → dict
```

**10 field properties per node:**

| Property | Range | Meaning |
|---|---|---|
| activation | [0, 1] | Current firing strength |
| ambiguity | [0, 1] | Local neighbourhood uncertainty |
| momentum | [0, 1] | Directional carry from previous activation |
| stability | [0, 1] | Consistency over time |
| resonance | [0, 1] | Alignment with field centroid |
| persistence | [0, 1] | Saturating growth with repeated exposure |
| novelty | [0, 1] | Freshness; inverse log(times_seen) |
| tension | [0, 1] | Semantic distance from field centroid |
| coherence | [0, 1] | Fraction of graph neighbours that are active |
| drift | [0, 1] | Magnitude of recent activation change |

**Ingest equations:**
```python
elapsed_min  = (now - node.updated_at) / 60
prev_act     = node.activation * (DECAY_RATE ** elapsed_min)
new_act      = prev_act + GAIN * (1 - prev_act)          # sigmoid saturation
momentum     = prev_act * 0.65 + old_momentum * 0.35
drift        = abs(new_act - prev_act)
stability    = old_stability * 0.92 + (1 - min(drift * 8, 1)) * 0.08
novelty      = 1 / (1 + log1p(times_seen))
persistence  = 1 - exp(-times_seen / 25)
```

**Key constants:**

| Constant | Value |
|---|---|
| GAIN | 0.30 (adaptive via Regulation) |
| DECAY_RATE | 0.90 default; range [0.10, 0.9990] |
| MIN_ACTIVE | 0.001 (prune floor) |
| MAX_NODES | 20,000 (in-memory cap) |

---

### 5.2 Field Dynamics

`src/field_dynamics.py` — Computes emergent properties requiring global field context. Runs after each article batch.

**Computed per-node:**

| Property | Computation |
|---|---|
| tension | Cosine distance from activation-weighted field centroid |
| resonance | 1 − tension |
| ambiguity | Std-dev of active neighbours' activations |
| coherence | Fraction of graph neighbours currently active |

**Activation propagation:**
```
For each active node (activation ≥ 0.05):
  → Top-6 neighbours by edge weight
  → delta = DIFFUSION × activation × edge_weight
  → Apply to neighbour (damped ×0.3)
```

Constants: `DIFFUSION = 0.12` (adaptive via Regulation), `TOP_ACTIVE = 200`, `PROPAGATE_K = 6`

---

## 6. Layer 4 — Higher-Order Cognition

### Episodes `src/episodes.py`

Records concept sequences; builds weighted transition graph.

**Episode schema:**
```json
{"id": "uuid", "ts": "ISO-8601", "concepts": [...], "ambiguity": 0.0, "outcome": "accepted"}
```

**Transition graph:** `"A__SEP__B"` key, `weight = weight × 0.95 + 1.0` on re-occurrence.

Config: `max_episodes=1000`, `min_concepts=2`, `transition_decay=0.95`

---

### Predictor `src/predictor.py`

Forecasts next concepts via transition graph. Pushes top predictions into working memory (`gain=0.08`) before retrieval.

---

### Contradiction Registry `src/contradiction.py`

Detects mutually incompatible activations. Types: `bidirectional | tension_clash | manual`.

Config: `detection_cosine_min=0.30`, `tension_min=0.40`, `max_contradictions=50`

---

### World Model `src/world_model.py`

Directed causal graph. Relations: `causes`, `suppresses`, `predicts`, `depends_on`. Lazy confidence decay: `conf × 0.98^(epoch_delta)`.

---

### Abstractor `src/abstractor.py`

Clusters co-occurring concepts into abstract nodes every 3 hours. Two hierarchy levels: `~name` (L1) and `~~name` (L2).

Config: `min_cooccurrence=5`, `min_cluster_size=3`, `emergence_threshold=0.60`

---

### Worldview `src/worldview.py`

Longitudinal identity portrait updated every 3 hours. Tracks: persistent concepts, chronic contradictions, surviving goals, home regions, foundational abstractions.

---

## 7. Layer 5 — Self-Regulation

### Regulation `src/regulation.py`

60-second tick that reads JAM field snapshot and adjusts three global scalars.

**Three regulated scalars:**

| Scalar | Bounds | Default | Effect |
|---|---|---|---|
| gain_rate | [0.10, 0.50] | 0.30 | Activation gain per observation |
| decay_rate | [0.10, 0.9990] | 0.90 | Per-minute activation retention |
| diffusion_strength | [0.05, 0.25] | 0.12 | Propagation fraction to neighbours |

**Mode election (6 modes):**

| Condition | Mode |
|---|---|
| coherence > 0.40 | focused |
| tension > 0.55 | conflicted |
| saturation > 0.70 | saturated |
| entropy_norm > 0.75 | exploratory |
| stability < 0.35 | drifting |
| else | associative |

**Goal election (5 goals):**

| Condition | Goal |
|---|---|
| tension > 0.5 | resolve_tension |
| coherence < 0.10 | expand_knowledge |
| coherence > 0.45 AND novelty < 0.3 | consolidate |
| entropy_norm > 0.70 | stabilise |
| else | explore |

**Scalar adjustment** (exponential blend α=0.15):
```python
target_gain      = default × (1 - pressure×0.4 + entropy_norm×0.2)
target_decay     = default - (pressure - 0.5) × 0.004
target_diffusion = default × (0.5 + entropy_norm)
new_val          = old × 0.85 + target × 0.15   # clamped to bounds
```

**Persistence:** `data/regulation.json`

---

## 8. Multimodal Pipeline

`src/multimodal.py` — Lightweight visual/audio fingerprinting. No transcription. No external model downloads.

**Image pipeline:**
```
Fetch URL → resize to 16×16 → flatten RGB → 768-dim pixel fingerprint
→ pad/truncate to 512 → orthogonal project to 384-dim
```

**Audio pipeline:**
```
Fetch URL → load audio (max 30s) → 128-band log-mel spectrogram
→ mean + std per band → 256-dim spectral fingerprint
→ pad to 512 → orthogonal project to 384-dim
```

**Projection matrix:** `data/mm_proj.npy` — 384×512, QR decomposition, seed=42. Generated once, reused.

**JAM integration (V8):**

The visual fingerprint is NOT inserted directly into the semantic graph (pixel space ≠ MiniLM semantic space). Instead, in `_process_multimodal()`:

```
1. embed_image(url) → 384-dim visual fingerprint
2. EmbeddingIndex.update([media_concept])    # store media node
3. EmbeddingIndex.knn(visual_vec, k=12)     # find 12 nearest text concepts
4. Create Concept objects from each neighbor (text + stored embedding)
5. JamField.ingest(neighbor_concepts)        # image "triggers" those concepts
```

The image acts as a perceptual retrieval key — it activates the semantically nearest text concepts in JAM, as if the image primed those ideas.

---

## 9. HTTP Server

`ui/server.py` — Pure-Python threaded HTTP server on port 8501. No Streamlit. No CDN dependencies.

**Threading:** `ThreadingMixIn + HTTPServer` with `daemon_threads = True`. Each request in its own thread.

**Fonts:** Served locally from `ui/assets/fonts/` (`Share Tech Mono` + `VT323`). No Google Fonts CDN.

### Navigation

6 tabs: **Core · Mind · Graph · Runner · Config** + always-visible status bar.

**Speed knob** (nav bar left) — controls learner throughput:

| Level | cycle_time | n_workers | topics_per_cycle |
|---|---|---|---|
| IDLE | 45s | 4 | 10 |
| LOW | 15s | 16 | 40 |
| MED | 0s | 64 | 80 |
| HIGH | 0s | 128 | 150 |
| MAX | 0s | 256 | 200 |

Writes to `data/engine_config.json` via `POST /api/set_speed`. Learner picks up changes live.

### API Reference

**GET endpoints:**

| Endpoint | Returns |
|---|---|
| `/api/state` | Global state, perf stats, live feed, currently reading |
| `/api/field` | JAM field snapshot, regulation scalars, cog_status |
| `/api/cognition` | Contradictions, transitions, episodes, worldview, abstractions |
| `/api/graph3d?n=N` | Force-graph format (nodes + links); 60s server-side cache |
| `/api/config` | Live engine config + tunnel status + is_local flag |
| `/api/runner/status?job_id=X` | Async job status + result + timing breakdown |
| `/health` | `"ok"` |

**POST endpoints:**

| Endpoint | Body | Returns |
|---|---|---|
| `/api/set_speed` | `{cycle_time, n_workers, topics_per_cycle}` | `{ok}` |
| `/api/runner/run` | `{prompt, model}` | `{job_id}` — async |
| `/api/runner/stream` | `{prompt, model}` | SSE stream of tokens |
| `/api/image/embed` | `{data: base64, label}` | `{ok, label}` |
| `/api/tunnel/start` | — | `{ok, error}` |
| `/api/tunnel/stop` | — | `{ok}` |
| `/api/ego/save\|load\|delete` | `{name}` | `{ok, message}` |
| `/api/config/save` | config dict | `{ok}` |
| `/api/learner/config` | partial config dict | `{ok}` |

### Graph Renderer

Canvas-based force-directed graph (pure JS, no D3/three.js).

- Default 200 nodes (options: 200 / 500 / 1k / 2k)
- Node radius max 9px
- Labels only appear when scale > 1.8×; hover shows full concept in bottom pill
- Nodes color-coded by activation: blue (high) → dim (low)
- Edges at 0.22 opacity

### Runner Pipeline

```
POST /api/runner/stream
  → _get_pipeline()          ~5ms   (cached)
  → extract(prompt)          ~0ms   (MiniLM cached)
  → detect_and_log()         ~1ms
  → build_prompt()           ~1ms
  → graph.update() + save()  ~1ms
  → call_llm() / stream      ~2-4s  (Ollama on GPU)
  → _feedback_async()        background (non-blocking)

Total: ~3s  (LLM is 99% of latency; all overhead ~10ms)
```

---

## 10. Data Files

| File | Written by | Contents |
|---|---|---|
| `data/embed_cache.npy` | EmbeddingIndex | Float32 embedding matrix |
| `data/embed_cache.meta.json` | EmbeddingIndex | Text list (parallel to .npy) |
| `data/jam_field.json` | JAM Field | Field snapshot (every decay) |
| `data/regulation.json` | Regulation | Scalars + mode/goal + tick count |
| `data/cog_status.json` | Regulation / Learner | `{mode, goal}` — overlay + sidebar |
| `data/learner_stats.json` | AutoLearner | `{total_concepts, total_sentences}` |
| `data/live_feed.jsonl` | AutoLearner | Rolling 500-entry activity log |
| `data/fetch_status.json` | AutoLearner | `{fetching, started_at}` |
| `data/currently_reading.json` | AutoLearner | `{title, source, goal, ts}` — written at START of fetch |
| `data/perf_profile.json` | AutoLearner | Pipeline timing per stage (rolling 50 samples) |
| `data/paused.txt` | UI | `"1"` paused / `"0"` running |
| `data/episodes.jsonl` | EpisodeStore | Concept co-occurrence episode log (max 1000) |
| `data/transitions.json` | EpisodeStore | Directed transition weights |
| `data/abstractions.json` | Abstractor | Abstract concept hierarchy L0/L1/L2 |
| `data/contradictions.json` | ContradictionRegistry | Open + resolved contradictions |
| `data/world_model.json` | WorldModel | Causal edge registry |
| `data/worldview.json` | Worldview | Longitudinal identity portrait |
| `data/mm_proj.npy` | multimodal | 384×512 orthogonal projection matrix |
| `data/instagram_session.json` | sources | instagrapi session cache |
| `data/engine_config.json` | Settings UI | All live params + ego presets + source_weights |
| `data/server.log` | server.py | Runtime log |
| `data/tunnel.pid` / `data/tunnel.log` | Settings UI | Cloudflare tunnel process |
| `logs/ambiguity_scores.jsonl` | Detector | All ambiguity detections |
| `logs/ab_log.jsonl` | Modulator | A/B comparison log |

---

## 11. config.yaml Reference

```yaml
model:              name, endpoint, stream
paths:              src, data, logs, snapshots, ui, db
embedding:          model, dim, similarity_threshold_merge, similarity_threshold_edge
ambiguity:          low_max, high_min, weights (variance/cluster/bridge)
modulation:         medium_neighbors, high_neighbors
decay:              daily_factor, prune_weight_below, orphan_days, reinforce_factor
meta_state:         decay_rate, reinforce_gain, active_threshold, max_concepts
temporal_memory:    working/episodic/semantic (decay_rate, gain, thresholds)
episodes:           max_episodes, min_concepts, transition_decay
predictor:          top_k, pre_activation_gain, min_transition_weight
contradiction:      detection_cosine_min, tension_min, max_contradictions
simulator:          max_steps, branch_factor
abstractor:         min_cooccurrence, min_cluster_size, emergence_threshold
world_model:        confidence_decay, min_confidence, max_edges, relations
regions:            suppression_factor, bridge_threshold, min_graph_size
prediction_error:   window, surprise_threshold, curiosity_boost
energy:             total, replenish_per_tick, costs
image_apis:         instagram, behance, unsplash, pexels, pixabay, flickr, deviantart
source_weights:     per-source weight overrides (engine_config.json takes precedence)
```

---

## 12. Key Architectural Decisions

| Decision | Rationale |
|---|---|
| ONNX Runtime GPU instead of PyTorch CUDA | PyTorch official CUDA wheels only go to Python 3.13; onnxruntime-gpu 1.26.0 has a cp314 wheel. ORT inference bypasses sentence_transformers' `batch_to_device()` which would fail on CPU-only torch. |
| CUDA DLLs via PATH not `add_dll_directory` | Windows DLL loader uses PATH for onnxruntime_providers_cuda.dll resolution; `os.add_dll_directory()` is insufficient. |
| `_OrtCudaEncoder` direct ORT inference | Bypasses sentence_transformers entirely; raw tokenizer + mean pooling; 8150 sent/sec on RTX 3050. |
| Multimodal as perceptual retrieval key | 16×16 pixel fingerprint lives in pixel space, not MiniLM semantic space. Direct insertion was random noise. k-NN retrieval uses it to activate the 12 nearest text concepts in JAM. |
| `get_embedding()` on EmbeddingIndex | Required to reconstruct full Concept objects for k-NN neighbors (text + embedding) without accessing private internals. |
| Decay rate range [0.10, 0.9990] | Allows Regulation to drive much faster concept decay (0.90 = fade in ~23min vs 0.9990 = many hours). Supports focused modes where stale concepts should clear quickly. |
| ThreadingMixIn on HTTPServer | Single-threaded HTTPServer blocked for up to 30s on `/api/graph3d` (2000-node traversal). ThreadingMixIn isolates each request in its own thread. |
| Per-handler try/except + `handle_error` stub | No single malformed request or connection abort can kill the server process. |
| Canvas force-directed graph (no iframe) | `localhost:8502` iframe required a separate Streamlit process. Self-contained canvas works with no external dependency. |
| Pipeline import caching | 4.3s cold-start → 9ms per runner call. |
| `keep_alive: -1` in Ollama | Model stays permanently in VRAM; no reload on idle. |
| `import torch FIRST` in extractor.py | Prevents sentence_transformers → transformers → torch circular import. |
| YTP local-only gate: check CF-Ray + CF-Connecting-IP | Cloudflare rewrites host to localhost; host check alone is insufficient. |
| Concept display filter ≤60 chars, no trailing period | Suppresses full sentences that pass the NLP pipeline with near-zero activation. |

---

## 13. Launching

```bat
# Full launch (recommended):
launch.bat
  → kill old processes
  → clear __pycache__
  → start overlay (pythonw overlay.py)
  → start server (python ui/server.py)
  → open http://localhost:8501

# Quick restart (no overlay):
restart.bat

# Overlay only:
overlay.bat
```

**Server startup sequence:**
1. `AutoLearner.start()` — all 4 threads
2. Warmup thread — pre-loads pipeline imports + pushes LLM into VRAM
3. Browser opens after 1s
