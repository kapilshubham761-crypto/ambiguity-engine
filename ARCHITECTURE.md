# Ambiguity Engine — Architecture Node Tree

This file IS the structural guide.
Every node = one file with one job.
Every arrow (──►) = an actual import.
If it is not in this tree, it does not belong in the project.

```
AMBIGUITY ENGINE
│
├── config.yaml                         ← weights, model, decay, embedding settings
│
├── ══ PIPELINE (src/) ══════════════════════════════════════════════════════════
│   │
│   ├── logger.py            [L]        ← shared structured logger
│   │       get_logger(name)            returns a file+console Logger
│   │       └──► used by [E][F][G][I][J]
│   │
│   ├── protocols.py         [0]        ← abstract interfaces (DIP contracts)
│   │       IGraph                      node_count, edge_count, update(), save()
│   │       IQueue                      add(), remove(), list(), size(), known_urls()
│   │       IHomework                   generate(), tick(), all(), pending()
│   │       ILessonSource               search(), fetch()
│   │       └──► imported by [D] so Teacher depends on shapes, not classes
│   │
│   ├── curriculum.py        [A]        ← pure data  (zero imports, zero logic)
│   │       CURRICULUM[]               8 stages: label, description, topics[]
│   │       STAGE_CONFIG[]             per-stage: sources, modifiers, min_readability
│   │       STAGE_TARGETS{}            min nodes + edges per stage (for grading)
│   │       └──► imported by [D][H][I] and shims
│   │
│   ├── sources.py           [B]        ← search + fetch  (7 source adapters)
│   │       SOURCES{}                  {name: (search_fn, fetch_fn)}
│   │       SOURCE_LABELS{}            emoji-prefixed display names
│   │       SOURCE_DESCRIPTIONS{}      one-liner per source
│   │       search_sources(q, srcs)    run search across selected sources
│   │       fetch_content(item)        lazy full-text fetch for one result
│   │       _flesch_score(text)        Flesch Reading Ease (readability gate)
│   │       _sentences(text)           spaCy sentence splitter
│   │       └──► imported by [D]
│   │
│   ├── queue_mgr.py         [C]        ← lesson queue CRUD
│   │       class LessonQueue          implements IQueue
│   │         add(lesson)
│   │         remove(lesson_id)
│   │         list() → list[dict]
│   │         size() → int
│   │         known_urls() → set[str]
│   │         get(lesson_id) → dict|None
│   │         clear()
│   │         reload()
│   │       └──► injected into [D] Teacher constructor
│   │
│   ├── homework.py          [H]        ← homework tracking
│   │       class HomeworkTracker      implements IHomework
│   │         generate(stage, graph)   scan coverage gaps → assign 15 topics
│   │         tick(topic)              mark done on accept
│   │         all() → list[dict]
│   │         pending() → list[dict]
│   │         pending_topics() → list[str]
│   │         reload()
│   │       └──► injected into [D] Teacher constructor
│   │             imports [A] curriculum (for topics list)
│   │
│   ├── teacher.py           [D]        ← ORCHESTRATOR  (runs 24/7)
│   │       REGIONS{}                  12-region spherical grid
│   │       _GRID[]                    3×4 display layout
│   │       load_prefs() / save_prefs()
│   │       class Teacher(queue: IQueue, homework: IHomework)
│   │         │
│   │         ├── Pause / Resume
│   │         │     is_paused (property)  reads paused.txt on every call
│   │         │     pause() / resume()
│   │         │
│   │         ├── Stage control
│   │         │     _current_stage()      reads discovery_stage.json
│   │         │     set_stage(index)      clears queue on stage change
│   │         │     advance_stage()
│   │         │
│   │         ├── Public read
│   │         │     .queue .status .stats .homework
│   │         │
│   │         ├── Lesson actions
│   │         │     accept(lesson_id, graph)
│   │         │       └─► [B] fetch_content()   lazy full-text fetch
│   │         │       └─► [E] extract()          concepts
│   │         │       └─► [G] detect_and_log()   ambiguity score
│   │         │       └─► [F] graph.update() + graph.save()
│   │         │     reject(lesson_id)
│   │         │
│   │         ├── Check-in  (every 3 hours)
│   │         │     check_in(graph)
│   │         │       └─► [I] assess() + save_card()
│   │         │       └─► [H] homework.generate()
│   │         │     next_checkin_in() → str
│   │         │
│   │         ├── Refill  (fast path — search only, no full fetch)
│   │         │     _refill()
│   │         │       └─► [B] SOURCES[src](query)   parallel threads
│   │         │       └─► writes fetch_status.json
│   │         │
│   │         └── Background thread
│   │               start(graph)
│   │               loop every 60s:
│   │                 1. sync paused flag from paused.txt
│   │                 2. gate on graph size (GRAPH_LIMIT_NODES = 150k)
│   │                 3. _refill() if queue < MIN_LESSONS (5)
│   │                 4. auto-accept all queued lessons (if enabled)
│   │                 5. _write_growth() → growth_log.jsonl
│   │                 6. check_in() if 3h elapsed
│   │
│   ├── extractor.py         [E]        ← concept extraction
│   │       Concept(text, embedding, source)   named tuple
│   │       extract(text) → list[Concept]
│   │         step 1  noun chunks         spaCy en_core_web_sm
│   │         step 2  named entities      PERSON, ORG, GPE, LOC …
│   │         step 3  fallback tokens     for short / abstract inputs
│   │         step 4  deduplicate + filter generics
│   │         step 5  batch embed         MiniLM all-MiniLM-L6-v2 (384-dim, cached)
│   │       └──► called by [D] teacher.accept(), [CLI] feed.py
│   │
│   ├── graph.py             [F]        ← semantic graph
│   │       class SemanticGraph          implements IGraph
│   │         update(concepts)           resolve/create nodes, update edges
│   │           _resolve_or_create()
│   │             string match           fast path
│   │             cosine ≥ 0.85         merge node
│   │             no match              new node (uuid)
│   │           _update_edges()
│   │             cosine < 0.05         skip
│   │             edge exists           weight × 1.10
│   │             new edge              weight = cosine similarity
│   │         save()                    → SQLite (nodes + edges tables)
│   │         snapshot()                → snapshots/YYYY-MM-DD.json
│   │         _load_from_db()           on startup
│   │       └──► called by [D] teacher, [J] maintenance, [CLI] feed.py
│   │             triggers [J] maintenance on __init__
│   │
│   ├── detector.py          [G]        ← ambiguity detection
│   │       AmbiguityResult(score, level, variance, cluster, bridge, …)
│   │       detect(concepts, graph) → AmbiguityResult
│   │         variance   mean pairwise cosine distance    weight 0.45
│   │         cluster    k=2 KMeans centroid separation   weight 0.45
│   │         bridge     graph neighbourhood divergence   weight 0.10
│   │         level      score < 0.30 → low
│   │                    score < 0.60 → medium
│   │                    score ≥ 0.60 → high
│   │       detect_and_log(text, concepts, graph) → logs + returns
│   │       └──► logs to logs/ambiguity_scores.jsonl
│   │             called by [D] teacher.accept(), [CLI] feed.py
│   │
│   ├── modulator.py         [K]        ← prompt modulation + A/B
│   │       ModulationResult(system_prompt, strategy, neighbours, …)
│   │       build_prompt(concepts, result, graph) → ModulationResult
│   │         low    → bare clean prompt
│   │         medium → inject 5 graph neighbours
│   │         high   → tension framing + 8 neighbours
│   │       call_llm(input, prompt, cfg) → str
│   │         └─► POST localhost:11434 (Ollama / Qwen 2.5 3B)
│   │       run_ab(input, concepts, result, graph) → (modulated, control)
│   │         └──► logs to logs/ab_log.jsonl
│   │       └──► called by [UI] 3_runner.py, 5_ab.py
│   │
│   ├── assessor.py          [I]        ← assessment + grading
│   │       Assessment(dataclass)        all fields typed
│   │       assess(graph, stage, prev) → Assessment
│   │         breadth      nodes / stage_target
│   │         depth        edge density + avg weight
│   │         activation   avg activation_count
│   │         calibration  ambiguity score spread (std-dev)
│   │         velocity     node/edge growth since last check-in
│   │         overall      GPA average → A+ … F
│   │         narrative    one-sentence teacher comment
│   │       save_card(assessment) / load_cards() → list[dict]
│   │       ensure_birthdate()
│   │       └──► imports STAGE_TARGETS from [A] curriculum
│   │             called by [D] teacher.check_in()
│   │
│   ├── maintenance.py       [J]        ← decay + pruning  (daily gate)
│   │       run_maintenance(graph, force=False) → summary dict
│   │         apply_decay()             weight × 0.99 ^ days_since_update
│   │         prune_weak_edges()        drop weight < 0.10
│   │         prune_orphan_nodes()      no edges for ≥ 14 days
│   │       preview_maintenance(graph) → dry-run dict (no changes)
│   │       └──► called by [F] graph.__init__() (daily gate via stamp file)
│   │
│   ├── ── CLI tools ──────────────────────────────────────────────────────────
│   │   └── feed.py                     ← batch feeder: text file → pipeline
│   │         feed(path, label)
│   │           for each line: [E]extract → [G]detect_and_log → [F]update
│   │           then [F]save + snapshot
│   │
│   ├── ── Shims (backward-compat re-exports) ─────────────────────────────────
│   │   ├── discover.py                 → from sources import *
│   │   ├── report_card.py              → from assessor import *
│   │   └── auto_discover.py            → imports [A][B], keeps AutoDiscovery class
│   │
│   └── ── Tests + Validation ─────────────────────────────────────────────────
│       ├── smoke_test.py
│       ├── validate_extractor.py
│       ├── validate_graph.py
│       ├── validate_detector.py
│       ├── validate_modulator.py
│       ├── validate_maintenance.py
│       ├── test_discover.py
│       └── eval_prompts.py
│
├── ══ UI (ui/) ══════════════════════════════════════════════════════════════
│   │
│   ├── _path.py                        ← sys.path bootstrap (imported by all pages)
│   │
│   ├── app.py                          ← ENTRY POINT + ROUTER
│   │     boot splash                   4s CSS animation, once per browser session
│   │     status detection              reads data/ files → (word, colour)
│   │     sidebar                       Stop/Resume + animated dot + fetch timer
│   │     st.navigation()               routes to _pages/
│   │     └──► uses components/status.py for status_word_and_colour()
│   │
│   ├── components/                     ← SHARED WIDGETS  (ISP: one concern per file)
│   │   │
│   │   ├── status.py                   ← engine status display
│   │   │     status_word_and_colour()  reads paused.txt + fetch_status.json
│   │   │     render_status_dot()       sidebar dot + Stop/Resume button
│   │   │     render_fetch_elapsed()    sidebar fetch timer line
│   │   │
│   │   ├── lesson_card.py              ← lesson card widget
│   │   │     render_lesson_card(lesson, teacher, graph)
│   │   │       Accept → teacher.accept() | Reject → teacher.reject()
│   │   │
│   │   └── grade_card.py               ← report card widgets
│   │         render_grade_row(card)    6-column grade metrics
│   │         render_syllabus(g, t)     curriculum milestone bars
│   │         render_homework_board(t)  pending + done assignments
│   │
│   └── _pages/
│         │
│         ├── 1_state.py     🔮 State
│         │     ① counters          nodes, edges, avg degree, last cleaned
│         │     ② ambiguity chart   last 60 scores from ambiguity_scores.jsonl
│         │     ③ maintenance       decay/prune sliders + dry-run preview
│         │     ④ top concepts      most-activated nodes table
│         │
│         ├── 2_graph.py     🕸️ Graph
│         │     controls      min-weight, max-nodes, brain filter, 3D, Split, Reset
│         │     subgraph      filter → slice → prune low edges
│         │     layout        spring_layout (2D) / random+z (3D large graphs)
│         │     split mode    remap x by brain side → LEFT | BRIDGE | RIGHT
│         │     figure        Plotly force-directed (2D or 3D)
│         │
│         ├── 3_runner.py    ▶️ Runner
│         │     ① extract     concepts via [E] extractor
│         │     ② detect      ambiguity via [G] detector
│         │     ③ modulate    system prompt via [K] modulator
│         │     ④ output      single LLM call or A/B side-by-side
│         │
│         ├── 4_timeline.py  📅 Timeline
│         │     ① growth chart   growth_log.jsonl → line chart
│         │     ② snapshot       browse any snapshots/YYYY-MM-DD.json
│         │
│         ├── 5_ab.py        🔀 A/B Evaluation
│         │     tab 1  Run Suite     30 eval prompts → ab_log.jsonl
│         │     tab 2  Blind Judge   random pair, no labels, pick winner
│         │     tab 3  Results       win rate by level (low/medium/high)
│         │     tab 4  Log           raw ab_log entries
│         │
│         ├── 6_discover.py  📚 Learn                (PRIMARY LESSON PAGE)
│         │     stage selector   8 curriculum stages + progress bar
│         │     location grid    12-region spherical map
│         │     year filter      publication year range
│         │     auto-accept      toggle autonomous graph growth
│         │     lesson queue     card grid → accept / reject
│         │     manual search    query any source → add to queue
│         │     teaching log     recent session history
│         │
│         ├── 7_learnings.py 📖 Learnings
│         │     tab 1  Concepts      searchable sortable node table
│         │     tab 2  Connections   weighted edges + strongest pairs
│         │     tab 3  Clusters      greedy modularity communities
│         │     tab 4  Ambiguity     every scored input with metrics
│         │
│         └── 9_report_card.py 📊 Report Card
│               birth certificate   age in days + node/edge totals
│               grade row           6 letter grades (uses grade_card)
│               teacher comment     one-line narrative
│               tab 1  Progress     node/edge growth + GPA line chart
│               tab 2  Syllabus     8 milestone cards (uses grade_card)
│               tab 3  Homework     pending/done assignments (uses grade_card)
│               tab 4  History      full card ledger table
│
├── ══ SUPPORT ══════════════════════════════════════════════════════════════
│   ├── overlay.py                      ← always-on-top stats window (tkinter)
│   │     CPU / RAM / nodes / edges / queue / stage / growth rate
│   │     updates every 2s from data/ files
│   ├── launch.bat                      ← overlay + Streamlit + open browser
│   └── restart.bat                     ← quick restart (no overlay, no browser)
│
└── ══ DATA  (runtime files, all gitignored) ════════════════════════════════
      data/
        graph.db               SQLite: nodes table + edges table
        teacher_queue.json     pre-fetched lesson queue (sentences lazy)
        teacher_stats.json     session history, totals (last 500)
        discovery_stage.json   current curriculum stage index {stage: N}
        homework.json          topic assignments + coverage scores
        report_cards.json      all assessment results (ledger)
        paused.txt             "1" = paused  |  "0" = running
        auto_accept.txt        "1" = autonomous  |  "0" = manual review
        fetch_status.json      {fetching, started_at, needed}
        growth_log.jsonl       append-only node/edge count snapshots
        search_prefs.json      {region, year_from, year_to}
        last_cleaned.txt       date stamp for daily maintenance gate
      logs/
        ambiguity_scores.jsonl  every detect_and_log() output
        ab_log.jsonl            modulated vs control response pairs
        ab_judgments.jsonl      blind judge decisions
        YYYY-MM-DD.log          structured pipeline logs
      snapshots/
        YYYY-MM-DD.json         daily graph exports
```

---

## Rules for this tree

1. **One node = one file = one job.** If a file has two jobs, split it.
2. **Every arrow is an import.** If you add an import not shown above, add the arrow.
3. **Shims are not nodes.** `discover.py`, `report_card.py`, `auto_discover.py` are
   thin re-export wrappers — they have no logic of their own.
4. **Tests live in their own branch.** They import from nodes but nothing imports them.
5. **UI pages import from `src/` via `sys.path`.** No page imports another page.
6. **UI components are shared widgets only.** No business logic inside `components/`.
7. **`data/` is the only shared state.** No module writes to another module's file.
8. **`config.yaml` is the only tuneable config.** No magic numbers scattered in code.
