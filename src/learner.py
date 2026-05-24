"""
AutoLearner — replaces Teacher + LessonQueue.

Direct pipeline: search -> fetch -> extract -> graph update.
No queue file. No manual review. 4 parallel workers. 10-second cycle.

Writes:
  data/live_feed.jsonl      last 200 activity entries (UI live monitor)
  data/learner_stats.json   cumulative totals
  data/fetch_status.json    fetching bool (sidebar dot)
  data/paused.txt           pause/resume sync
"""

from __future__ import annotations
import json, os, random, sys, threading, time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DATA = _ROOT / 'data'
sys.path.insert(0, str(Path(__file__).parent))

from sources import SOURCES, fetch_content, _sentences
from logger  import get_logger

log = get_logger('learner')

# ── Config — defaults (overridden at runtime by engine_config.json) ───────────
CYCLE_TIME       = 10
N_WORKERS        = 8
TOPICS_PER_CYCLE = 12
MIN_SENTENCES    = 3
FETCH_TIMEOUT    = 12
SEARCH_TIMEOUT   = 7
CHECKIN_EVERY    = 3 * 3600
FEED_MAX         = 200

def _lcfg(key: str, default):
    """Read a learning param from live config, fall back to module default."""
    try:
        from config import Config
        v = Config.get_instance().get('learning', key)
        return type(default)(v) if v is not None else default
    except Exception:
        return default

# ── Paths ─────────────────────────────────────────────────────────────────────
_STATS_PATH  = _DATA / 'learner_stats.json'
_FEED_PATH   = _DATA / 'live_feed.jsonl'
_PAUSED_PATH = _DATA / 'paused.txt'
_FSTATUS     = _DATA / 'fetch_status.json'

# ── Flat topic list — searched in random order each cycle ─────────────────────
TOPICS = [
    'consciousness perception cognition', 'quantum mechanics wave particle',
    'evolution natural selection adaptation', 'language syntax semantics meaning',
    'thermodynamics entropy energy systems', 'philosophy of mind identity self',
    'mathematics topology abstract algebra', 'ecology ecosystems biodiversity',
    'astrophysics black holes spacetime', 'economics game theory decision making',
    'genetics DNA protein expression', 'history civilisation collapse empire',
    'psychology behaviour motivation', 'climate change atmospheric physics',
    'neuroscience memory learning brain', 'sociology culture social structures',
    'ethics morality free will determinism', 'chemistry molecular bonds reactions',
    'literature narrative symbolism metaphor', 'music harmony rhythm acoustics',
    'art perception aesthetics creativity', 'political theory power governance',
    'medicine disease immunity biology', 'physics relativity spacetime curvature',
    'oceanography fluid dynamics tides', 'anthropology human origins culture',
    'logic inference formal systems', 'optics light photons electromagnetism',
    'materials science crystalline structure', 'epidemiology population health risk',
    'linguistics phonology grammar pragmatics', 'cognitive science mental models',
    'mythology symbolism archetype narrative', 'architecture space form structure',
    'food systems agriculture soil microbiome', 'sleep dreams unconscious mind',
    'time perception duration memory', 'emergence complexity self-organisation',
    'chaos theory dynamical systems bifurcation', 'topology knots manifolds geometry',
    'geopolitics power conflict diplomacy', 'biochemistry metabolism pathways',
    'ancient history religion ritual belief', 'poetry verse language rhythm',
    'human emotion grief joy love longing', 'philosophy of language meaning truth',
    'evolutionary biology animal behaviour', 'social psychology group identity',
]

# Keywords that flag content as computer-code-related — skip these items entirely
_CODE_KEYWORDS = {
    'programming', 'coding', 'source code', 'github', 'repository', 'javascript',
    'typescript', 'nodejs', 'reactjs', 'angularjs', 'vuejs', 'webpack', 'npm ',
    ' pip ', 'docker', 'kubernetes', 'devops', 'software development', 'web development',
    'stack overflow', 'stackoverflow', 'api documentation', 'code tutorial',
    'learn to code', 'how to program', 'programming language', 'software engineering',
    'machine learning library', 'deep learning framework', 'neural network library',
    'python library', 'python package', 'java tutorial', 'c++ tutorial',
    'html css', 'css framework', 'rest api', 'graphql', 'microservices',
    'database schema', 'sql tutorial', 'git tutorial', 'debugging',
    'open source project', 'code review', 'pull request', 'version control',
}


def _is_code_content(item: dict) -> bool:
    """Return True if this item is about programming/code and should be skipped."""
    text = (item.get('title', '') + ' ' + item.get('snippet', '')).lower()
    return any(kw in text for kw in _CODE_KEYWORDS)


def _fstatus_write(fetching: bool) -> None:
    try:
        data = {
            'fetching':   fetching,
            'started_at': datetime.now(tz=timezone.utc).isoformat() if fetching else None,
            'needed':     0,
        }
        tmp = _FSTATUS.with_suffix('.tmp')
        tmp.write_text(json.dumps(data), encoding='utf-8')
        tmp.replace(_FSTATUS)
    except Exception:
        pass


class AutoLearner:
    _instance: 'AutoLearner | None' = None

    @classmethod
    def get(cls) -> 'AutoLearner':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    _PERF_PATH = _DATA / 'perf_profile.json'

    def __init__(self):
        _DATA.mkdir(exist_ok=True)
        self._stats        = self._load_stats()
        self._last_checkin = self._load_last_checkin()
        self._thread: threading.Thread | None = None
        self._graph        = None
        self._known_urls: set = set()
        self._feed_lock    = threading.Lock()
        self._perf_lock    = threading.Lock()
        self._perf_samples: list[dict] = []   # per-article timings (last 50)
        self._subsys_buf:  list[str]   = []   # texts queued for background subsystems
        self._subsys_lock  = threading.Lock()
        # Feed buffer — in-memory deque, flushed every 5 appends
        try:
            existing = _FEED_PATH.read_text(encoding='utf-8').splitlines() if _FEED_PATH.exists() else []
            self._feed_buf: deque[str] = deque(existing[-FEED_MAX:], maxlen=FEED_MAX)
        except Exception:
            self._feed_buf = deque(maxlen=FEED_MAX)
        self._feed_dirty = 0

    def _ensure_graph(self) -> None:
        if self._graph is None:
            try:
                from graph import SemanticGraph
                self._graph = SemanticGraph()
            except Exception:
                pass

    # ── persistence ───────────────────────────────────────────────────────────

    def _load_stats(self) -> dict:
        try:
            return json.loads(_STATS_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {'total_concepts': 0, 'total_sentences': 0}

    def _save_stats(self) -> None:
        tmp = _STATS_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._stats, indent=2), encoding='utf-8')
        tmp.replace(_STATS_PATH)

    def _feed_append(self, entry: dict) -> None:
        try:
            with self._feed_lock:
                self._feed_buf.append(json.dumps(entry, ensure_ascii=False))
                self._feed_dirty += 1
                if self._feed_dirty >= 5:
                    _FEED_PATH.write_text('\n'.join(self._feed_buf), encoding='utf-8')
                    self._feed_dirty = 0
        except Exception:
            pass

    def _feed_flush(self) -> None:
        """Force write the feed buffer — call at end of each cycle."""
        try:
            with self._feed_lock:
                if self._feed_dirty > 0:
                    _FEED_PATH.write_text('\n'.join(self._feed_buf), encoding='utf-8')
                    self._feed_dirty = 0
        except Exception:
            pass

    def _load_last_checkin(self) -> float:
        return 0.0

    # ── pause / resume ────────────────────────────────────────────────────────

    @property
    def is_paused(self) -> bool:
        try:
            return _PAUSED_PATH.read_text().strip() == '1'
        except Exception:
            return False

    def pause(self)  -> None: _PAUSED_PATH.write_text('1')
    def resume(self) -> None: _PAUSED_PATH.write_text('0')

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── process one search result ─────────────────────────────────────────────

    def _subsys_queue(self, texts: list[str]) -> None:
        """Add texts to the background subsystem buffer."""
        with self._subsys_lock:
            self._subsys_buf.extend(texts)
            if len(self._subsys_buf) > 2000:
                self._subsys_buf = self._subsys_buf[-2000:]

    def _regulator_worker(self) -> None:
        """Background thread: runs MetaRegulator every 60s — autonomous parameter control."""
        time.sleep(60)   # let system stabilise before first regulation
        while True:
            try:
                from regulator import MetaRegulator
                MetaRegulator.get().tick()
            except Exception as e:
                log.debug('regulator error: %s', e)
            time.sleep(60)

    def _subsys_worker(self) -> None:
        """Background thread: drains subsystem buffer every 30s — never blocks the hot path."""
        while True:
            time.sleep(30)
            with self._subsys_lock:
                if not self._subsys_buf:
                    continue
                texts = list(self._subsys_buf)
                self._subsys_buf.clear()
            def _s(fn):
                try: fn()
                except Exception: pass
            _s(lambda: __import__('meta_state').MetaState.get().reinforce(texts))
            _s(lambda: __import__('memory').TemporalMemory.get().reinforce(texts))
            _s(lambda: __import__('contradiction').ContradictionRegistry.get().observe(texts))
            _s(lambda: __import__('world_model').WorldModel.get().infer_from_context(texts))
            _s(lambda: __import__('ecology').CognitiveEcology.get().tick(texts))
            try:
                from episodes  import EpisodeStore
                from predictor import Predictor
                from memory    import TemporalMemory
                EpisodeStore.get().record(texts, ambiguity=0.0, region=None)
                Predictor.get().pre_activate(texts, TemporalMemory.get())
            except Exception:
                pass

    def _process(self, item: dict) -> dict:
        from detector import detect_and_log
        _T = time.perf_counter

        entry = {
            'ts':      datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            'topic':   item.get('topic', ''),
            'title':   item.get('title', '')[:80],
            'source':  item.get('source', ''),
            'url':     item.get('url', ''),
            'concepts': 0,
            'sentences': 0,
            'status':  'ok',
        }

        # Signal immediately — bar shows this before fetch even completes
        try:
            _cr = _DATA / 'currently_reading.json'
            _cr.write_text(json.dumps({
                'title':  item.get('title', '')[:160],
                'source': item.get('source', ''),
                'ts':     datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            }), encoding='utf-8')
        except Exception:
            pass

        t0 = _T()
        try:
            sentences = fetch_content(item)
        except Exception:
            sentences = _sentences(item.get('snippet', ''), min_len=20)
        t_fetch = _T() - t0

        if len(sentences) < _lcfg('min_sentences', MIN_SENTENCES):
            entry['status'] = 'skip:short'
            return entry

        # Batch-embed all sentences in one encoder call, then update graph per sentence
        from extractor import extract_batch
        all_concepts = []
        all_texts    = []
        t_extract = t_graph = 0.0

        t1 = _T()
        batch = extract_batch(sentences)
        t_extract = _T() - t1

        _cr_path = _DATA / 'currently_reading.json'
        n_total   = len(sentences)
        for idx, concepts in enumerate(batch):
            # Update live bar with current sentence text
            try:
                sent_text = sentences[idx][:120] if idx < len(sentences) else ''
                _cr_path.write_text(json.dumps({
                    'title':    item.get('title', '')[:160],
                    'source':   item.get('source', ''),
                    'sentence': sent_text,
                    'progress': f'{idx + 1}/{n_total}',
                    'ts':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
                }), encoding='utf-8')
            except Exception:
                pass
            if not concepts:
                continue
            if self._graph:
                t1 = _T(); self._graph.update(concepts); t_graph += _T() - t1
            all_concepts.extend(concepts)
            all_texts.extend(c.text for c in concepts)

        if not all_texts:
            entry['status'] = 'skip:no-concepts'
            return entry

        n_concepts = len(all_concepts)

        # Detect ONCE per article on a sample — not per sentence
        t1 = _T()
        sample = all_concepts[:30] if len(all_concepts) > 30 else all_concepts
        detect_and_log(item.get('title', ''), sample, graph=self._graph)
        t_detect = _T() - t1

        t1 = _T()
        if self._graph:
            self._graph.save()
        t_save = _T() - t1

        # Queue subsystems for background processing — does not block
        self._subsys_queue(all_texts)
        t_subsys = 0.0

        t_total = _T() - t0

        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + len(sentences)
        self._stats['total_concepts']  = self._stats.get('total_concepts', 0) + n_concepts
        self._save_stats()

        entry['concepts']  = n_concepts
        entry['sentences'] = len(sentences)

        sample_dict = {
            'fetch':    round(t_fetch,   3),
            'extract':  round(t_extract, 3),
            'detect':   round(t_detect,  3),
            'graph':    round(t_graph,   3),
            'save':     round(t_save,    3),
            'subsys':   round(t_subsys,  3),
            'total':    round(t_total,   3),
            'sentences': len(sentences),
            'concepts':  n_concepts,
            'source':    item.get('source', ''),
        }
        with self._perf_lock:
            self._perf_samples.append(sample_dict)
            if len(self._perf_samples) > 50:
                self._perf_samples = self._perf_samples[-50:]

        return entry

    # ── one full cycle ────────────────────────────────────────────────────────

    def _write_perf_profile(self, cycle: dict) -> None:
        with self._perf_lock:
            samples = list(self._perf_samples)
        if not samples:
            return
        keys = ('fetch', 'extract', 'detect', 'graph', 'save', 'subsys', 'total')
        avg  = {k: round(sum(s.get(k, 0) for s in samples) / len(samples), 3) for k in keys}
        tot  = avg['total'] or 1
        pct  = {k: round(avg[k] / tot * 100, 1) for k in keys if k != 'total'}
        profile = {
            'updated':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            'n_samples':     len(samples),
            'process_avg_s': avg,
            'process_pct':   pct,
            'cycle':         cycle,
        }
        try:
            with open(self._PERF_PATH, 'w', encoding='utf-8') as f:
                json.dump(profile, f, indent=2)
        except Exception as e:
            log.debug('perf write failed: %s', e)

    def _cycle(self) -> None:
        self._ensure_graph()
        n_workers        = _lcfg('n_workers',        N_WORKERS)
        topics_per_cycle = _lcfg('topics_per_cycle', TOPICS_PER_CYCLE)
        fetch_timeout    = _lcfg('fetch_timeout',     FETCH_TIMEOUT)
        search_timeout   = _lcfg('search_timeout',    SEARCH_TIMEOUT)

        all_sources = list(SOURCES.keys())
        topics = random.sample(TOPICS, min(topics_per_cycle, len(TOPICS)))

        _src_weights = [3 if s == 'wikipedia' else 1 for s in all_sources]

        def _search(topic):
            src = random.choices(all_sources, weights=_src_weights, k=1)[0]
            try:
                search_fn, _ = SOURCES[src]
                results = search_fn(topic, max_results=3)
                for r in results:
                    r['topic']  = topic
                    r['source'] = src
                return results
            except Exception:
                return []

        items_to_process = []
        _fstatus_write(True)

        tc0 = time.perf_counter()
        _search_pool = ThreadPoolExecutor(max_workers=n_workers)
        try:
            for fut in as_completed([_search_pool.submit(_search, t) for t in topics],
                                    timeout=search_timeout * 2):
                try:
                    for item in fut.result():
                        url = item.get('url', '')
                        if url and url not in self._known_urls and not _is_code_content(item):
                            items_to_process.append(item)
                            self._known_urls.add(url)
                except Exception:
                    pass
        except Exception:
            log.debug('search phase timed out')
        finally:
            _search_pool.shutdown(wait=False, cancel_futures=True)
        t_search = time.perf_counter() - tc0

        if len(self._known_urls) > 5000:
            self._known_urls = set(list(self._known_urls)[-2000:])

        n_ok = 0
        tp0 = time.perf_counter()
        _process_pool = ThreadPoolExecutor(max_workers=n_workers)
        futs = {_process_pool.submit(self._process, item): item
                for item in items_to_process}
        try:
            for fut in as_completed(futs, timeout=fetch_timeout * 3):
                if self.is_paused:
                    break
                try:
                    entry = fut.result()
                    if entry['status'] == 'ok':
                        n_ok += 1
                        self._feed_append(entry)
                        log.info('learned: %s [%s] +%d concepts',
                                 entry['title'], entry['source'], entry['concepts'])
                except Exception:
                    pass
        except Exception:
            log.debug('process phase timed out after %ds', fetch_timeout * 3)
        finally:
            _process_pool.shutdown(wait=False, cancel_futures=True)
        t_process = time.perf_counter() - tp0

        _fstatus_write(False)

        # Growth snapshot
        if self._graph:
            try:
                p = _DATA / 'growth_log.jsonl'
                line = json.dumps({
                    'ts':    datetime.now(tz=timezone.utc).isoformat(timespec='minutes'),
                    'nodes': self._graph.node_count,
                    'edges': self._graph.edge_count,
                })
                with open(p, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except Exception:
                pass

        # Abstractor + Worldview periodic run (replaces old check-in)
        checkin_every = _lcfg('checkin_every', CHECKIN_EVERY)
        if time.time() - self._last_checkin >= checkin_every and self._graph:
            try:
                from abstractor import Abstractor
                from worldview  import Worldview
                Abstractor.get().run()
                Worldview.get().update()
                self._last_checkin = time.time()
                log.info('abstractor + worldview refresh complete')
            except Exception as e:
                log.debug('refresh error: %s', e)

        # Cognitive ticks
        def _t(fn):
            try: fn()
            except Exception: pass

        tt0 = time.perf_counter()
        _t(lambda: __import__('stability').StabilityMonitor.get().tick(
            __import__('meta_state').MetaState.get()))
        _t(lambda: __import__('goals').GoalEngine.get().tick())
        _t(lambda: __import__('reflection').ReflectionMonitor.get().report())
        _t(lambda: __import__('memory').TemporalMemory.get().decay_to())
        _t(lambda: __import__('energy').EnergyBudget.get().replenish())
        _t(lambda: __import__('self_model').SelfModel.get().tick())
        _t(lambda: __import__('identity').IdentityTracker.get().observe())
        _t(lambda: __import__('meta_learning').MetaLearner.get().tick())
        _t(lambda: __import__('evolver').Evolver.get().tick())
        _t(lambda: __import__('novelty').NoveltyTracker.get().snapshot_top5(
            __import__('meta_state').MetaState.get()))
        _t(lambda: __import__('meta_state').MetaState.get().decay_to())
        t_ticks = time.perf_counter() - tt0

        # Flush feed buffer so UI sees all entries from this cycle
        self._feed_flush()

        # Write performance profile
        self._write_perf_profile({
            't_search_s':  round(t_search,  2),
            't_process_s': round(t_process, 2),
            't_ticks_s':   round(t_ticks,   2),
            't_total_s':   round(t_search + t_process + t_ticks, 2),
            'items_found': len(items_to_process),
            'items_ok':    n_ok,
            'workers':     n_workers,
        })

        # Write cog_status for overlay
        try:
            _mode = __import__('stability').StabilityMonitor.get()._current_mode
            _goal = __import__('goals').GoalEngine.get().current_goal().replace('_', ' ')
            _cs = _DATA / 'cog_status.json'
            _tmp = _cs.with_suffix('.tmp')
            _tmp.write_text(json.dumps({'mode': _mode, 'goal': _goal}), encoding='utf-8')
            _tmp.replace(_cs)
        except Exception:
            pass

    # ── background thread ─────────────────────────────────────────────────────

    def start(self, graph=None) -> None:
        if self._thread and self._thread.is_alive():
            return
        if graph is not None:
            self._graph = graph

        def _worker():
            log.info('AutoLearner started (cycle=%ds workers=%d)', CYCLE_TIME, N_WORKERS)
            while True:
                try:
                    if self.is_paused:
                        _fstatus_write(False)
                    else:
                        self._cycle()
                except Exception as e:
                    log.debug('cycle error: %s', e)
                    _fstatus_write(False)
                time.sleep(_lcfg('cycle_time', CYCLE_TIME))

        self._thread = threading.Thread(target=_worker, daemon=True, name='learner')
        self._thread.start()
        threading.Thread(target=self._subsys_worker,   daemon=True, name='subsys').start()
        threading.Thread(target=self._regulator_worker, daemon=True, name='regulator').start()
