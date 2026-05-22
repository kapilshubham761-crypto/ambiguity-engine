# Ambiguity Engine — Project Context

## What This Is
A local, autonomous AI learning system that teaches itself by fetching real content from the internet, extracting semantic concepts, and building a knowledge graph — like a child progressing through school.

No cloud AI. No paid APIs. Runs entirely on the user's machine.

## How It Works (end-to-end)
1. **Teacher** (`src/teacher.py`) runs a background thread 24/7
2. It fetches lessons from Wikipedia, arXiv, Gutenberg, Reddit, OpenAlex, web
3. Content is filtered by Flesch readability score (age-appropriate per curriculum stage)
4. User reviews lessons in the **Learn** page — Accept feeds sentences into the graph, Reject discards
5. Sentences → concept extraction (`src/extractor.py`) → semantic graph (`src/graph.py`)
6. Every 3 hours: automatic report card assessment (`src/report_card.py`)
7. Overlay (`overlay.py`) shows live CPU/RAM/engine stats always-on-top

## Tech Stack
- **Backend**: Python, NetworkX (graph), spaCy (NLP), SQLite (graph persistence)
- **Frontend**: Streamlit multi-page app (`ui/app.py` + `ui/_pages/`)
- **Content sources**: Wikipedia, Simple Wikipedia, arXiv, Gutenberg, Reddit, OpenAlex, DuckDuckGo+trafilatura
- **Overlay**: tkinter, psutil
- **Virtual env**: `.venv/` — always use `.venv/Scripts/python` or `.venv/Scripts/streamlit`

## Project Structure
```
ambiguity-engine/
├── src/
│   ├── teacher.py        # Core: background fetch loop, queue, pause/resume, stage mgmt
│   ├── discover.py       # Multi-source search + fetch (Wikipedia, arXiv, etc.)
│   ├── auto_discover.py  # CURRICULUM (8 stages) + STAGE_CONFIG (per-stage sources/readability)
│   ├── graph.py          # SemanticGraph — NetworkX + SQLite persistence
│   ├── extractor.py      # Concept extraction from sentences
│   ├── detector.py       # Ambiguity detection + logging
│   ├── report_card.py    # Periodic assessment of graph knowledge
│   └── maintenance.py    # Daily graph decay + pruning
├── ui/
│   ├── app.py            # Streamlit entry point — sidebar, status dot, navigation
│   └── _pages/           # Pages (underscore prefix = NOT auto-discovered by Streamlit)
│       ├── 1_state.py    # Current graph state
│       ├── 2_graph.py    # 3D graph visualisation
│       ├── 3_runner.py   # Manual runner
│       ├── 4_timeline.py # Snapshot timeline
│       ├── 5_ab.py       # A/B testing
│       ├── 6_discover.py # Learn page — lesson queue, batch accept/reject
│       ├── 7_learnings.py# Accepted lessons log
│       └── 9_report_card.py # Assessment history
├── data/                 # Runtime files (gitignored)
│   ├── graph.db          # SQLite graph
│   ├── teacher_queue.json# Pre-fetched lesson queue (includes full sentences)
│   ├── teacher_stats.json# Session history, totals
│   ├── fetch_status.json # Live fetch progress (started_at, fetching bool)
│   ├── discovery_stage.json # Current curriculum stage index
│   ├── search_prefs.json # Region + year filter preferences
│   ├── paused.txt        # "1" = paused, "0" = running (cross-process sync)
│   └── report_cards.json # Assessment history
├── overlay.py            # Always-on-top stats overlay (tkinter)
├── launch.bat            # ONE-CLICK launcher: kills old → clears cache → starts overlay + Streamlit → opens browser
├── restart.bat           # Quick restart (no overlay, no browser open)
├── overlay.bat           # Launch overlay only (uses pythonw — no console)
└── CLAUDE.md             # This file
```

## Key Conventions
- **Always use `.venv/Scripts/python`** — not system python
- **Pages must be in `ui/_pages/`** (underscore prefix) — `ui/pages/` would be auto-discovered by Streamlit and create duplicate nav
- **Paused state** is `data/paused.txt` — write "1" to pause, "0" to resume; Teacher reads it every cycle AND `is_paused` property reads it live on every call
- **Queue saves full lessons** including sentences — never strip sentences on save (caused KeyError bug before)
- **fetch_status.json** is written by `_refill()` at start/end and cleared when paused — used by sidebar for elapsed time display
- **No coding features in the Streamlit app** — no git panels, terminals, or code editors (user codes in VS Code)

## Curriculum Stages (8 total)
| Stage | Label | Readability |
|-------|-------|-------------|
| 0 | Ages 1–5 | ≥75 (Simple Wiki + children's Gutenberg only) |
| 1 | Year 1 (Early School) | ≥65 |
| 2 | Year 2–3 | ≥55 |
| 3 | Year 4–6 (Primary) | ≥45 |
| 4 | Middle School | ≥35 |
| 5 | High School | ≥25 |
| 6 | Undergraduate | ≥15 |
| 7 | Graduate/Research | 0 (arXiv, OpenAlex unlocked) |

## Launching the App
```
# Full launch (recommended):
double-click launch.bat

# Manual:
cd ui
..\.venv\Scripts\streamlit run app.py

# Overlay only:
double-click overlay.bat
```

## Current Status
All 8 phases complete and running. Recent work:
- Unified Discover + Teacher into single Learn page
- Age-appropriate content filtering (Flesch score + per-stage source lists)
- Location filter (12-region spherical grid) + year range filter
- Batch lesson selection (☑ All / Accept N / Reject N)
- Always-on-top overlay with CPU/RAM/graph stats
- Sidebar animated status dot with fetch elapsed time + thinking line
- Stop/Resume button visible on all pages (writes paused.txt directly)
- Per-fetch 20s timeout — hung sources are skipped automatically
- Paused state syncs instantly across all pages (reads file on every call)

## Pending / Known Issues
- Graph visualisation (2_graph.py) doesn't auto-refresh — manual reload needed
- No GPU monitoring in overlay yet
- Report card assessment is rule-based (coverage %) not LLM-powered

## GitHub
https://github.com/kapilshubham761-crypto/ambiguity-engine
