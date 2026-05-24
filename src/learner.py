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
    'thermodynamics entropy energy systems', 'machine learning neural networks',
    'philosophy of mind identity self', 'mathematics topology abstract algebra',
    'ecology ecosystems biodiversity', 'astrophysics black holes spacetime',
    'economics game theory decision making', 'genetics DNA protein expression',
    'history civilisation collapse empire', 'psychology behaviour motivation',
    'computer science algorithms complexity', 'climate change atmospheric physics',
    'neuroscience memory learning brain', 'sociology culture social structures',
    'ethics morality free will determinism', 'chemistry molecular bonds reactions',
    'literature narrative symbolism metaphor', 'music harmony rhythm acoustics',
    'art perception aesthetics creativity', 'political theory power governance',
    'medicine disease immunity biology', 'physics relativity spacetime curvature',
    'robotics automation embodied intelligence', 'oceanography fluid dynamics tides',
    'anthropology human origins culture', 'logic inference formal systems',
    'optics light photons electromagnetism', 'materials science crystalline structure',
    'epidemiology population health risk', 'linguistics phonology grammar pragmatics',
    'information theory entropy signal noise', 'cognitive science mental models',
    'mythology symbolism archetype narrative', 'architecture space form structure',
    'food systems agriculture soil microbiome', 'sleep dreams unconscious mind',
    'time perception duration memory', 'emergence complexity self-organisation',
    'chaos theory dynamical systems bifurcation', 'topology knots manifolds geometry',
    'geopolitics power conflict diplomacy', 'biochemistry metabolism pathways',
]


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

    def __init__(self):
        _DATA.mkdir(exist_ok=True)
        self._stats        = self._load_stats()
        self._last_checkin = self._load_last_checkin()
        self._thread: threading.Thread | None = None
        self._graph        = None
        self._known_urls: set = set()
        self._feed_lock    = threading.Lock()

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
                existing = _FEED_PATH.read_text(encoding='utf-8').splitlines() if _FEED_PATH.exists() else []
                existing.append(json.dumps(entry, ensure_ascii=False))
                _FEED_PATH.write_text('\n'.join(existing[-FEED_MAX:]), encoding='utf-8')
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

    def _process(self, item: dict) -> dict:
        from extractor import extract
        from detector  import detect_and_log

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

        try:
            sentences = fetch_content(item)
        except Exception:
            sentences = _sentences(item.get('snippet', ''), min_len=20)

        if len(sentences) < _lcfg('min_sentences', MIN_SENTENCES):
            entry['status'] = 'skip:short'
            return entry

        all_texts = []
        n_concepts = 0
        for sent in sentences:
            concepts = extract(sent)
            if not concepts:
                continue
            detect_and_log(sent, concepts, graph=self._graph)
            if self._graph:
                self._graph.update(concepts)
            n_concepts += len(concepts)
            all_texts.extend(c.text for c in concepts)

        if not all_texts:
            entry['status'] = 'skip:no-concepts'
            return entry

        if self._graph:
            self._graph.save()

        def _s(fn):
            try: fn()
            except Exception: pass

        _s(lambda: __import__('meta_state').MetaState.get().reinforce(all_texts))
        _s(lambda: __import__('memory').TemporalMemory.get().reinforce(all_texts))
        _s(lambda: __import__('contradiction').ContradictionRegistry.get().observe(all_texts))
        _s(lambda: __import__('world_model').WorldModel.get().infer_from_context(all_texts))
        _s(lambda: __import__('ecology').CognitiveEcology.get().tick(all_texts))

        try:
            from episodes  import EpisodeStore
            from predictor import Predictor
            from memory    import TemporalMemory
            EpisodeStore.get().record(all_texts, ambiguity=0.0, region=None)
            Predictor.get().pre_activate(all_texts, TemporalMemory.get())
        except Exception:
            pass

        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + len(sentences)
        self._stats['total_concepts']  = self._stats.get('total_concepts', 0) + n_concepts
        self._save_stats()

        entry['concepts']  = n_concepts
        entry['sentences'] = len(sentences)
        return entry

    # ── one full cycle ────────────────────────────────────────────────────────

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

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            for fut in as_completed([pool.submit(_search, t) for t in topics],
                                    timeout=search_timeout * 2):
                try:
                    for item in fut.result():
                        url = item.get('url', '')
                        if url and url not in self._known_urls:
                            items_to_process.append(item)
                            self._known_urls.add(url)
                except Exception:
                    pass

        if len(self._known_urls) > 5000:
            self._known_urls = set(list(self._known_urls)[-2000:])

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(self._process, item): item
                    for item in items_to_process}
            for fut in as_completed(futs, timeout=fetch_timeout * 3):
                if self.is_paused:
                    break
                try:
                    entry = fut.result()
                    if entry['status'] == 'ok':
                        self._feed_append(entry)
                        log.info('learned: %s [%s] +%d concepts',
                                 entry['title'], entry['source'], entry['concepts'])
                except Exception:
                    pass

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
