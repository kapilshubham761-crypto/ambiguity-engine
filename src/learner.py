"""
AutoLearner — JAM Field pipeline
==================================
fetch → embed → graph → JAM field ingest → field dynamics

Background threads:
  learner   — main cycle every 10s
  subsys    — contradiction + world_model + episodes every 30s
  regulator — Regulation.tick() every 60s
"""

from __future__ import annotations

import json, os, random, re, sys, threading, time
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

# ── Config defaults ────────────────────────────────────────────────────────────
CYCLE_TIME       = 10
N_WORKERS        = 8
TOPICS_PER_CYCLE = 12
MIN_SENTENCES    = 3
FETCH_TIMEOUT    = 12
SEARCH_TIMEOUT   = 7
CHECKIN_EVERY    = 3 * 3600
FEED_MAX         = 200

def _lcfg(key: str, default):
    try:
        from config import Config
        v = Config.get_instance().get('learning', key)
        return type(default)(v) if v is not None else default
    except Exception:
        return default

# ── Paths ──────────────────────────────────────────────────────────────────────
_STATS_PATH  = _DATA / 'learner_stats.json'
_FEED_PATH   = _DATA / 'live_feed.jsonl'
_PAUSED_PATH = _DATA / 'paused.txt'
_FSTATUS     = _DATA / 'fetch_status.json'

# ── Topics ─────────────────────────────────────────────────────────────────────
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

# ── Code-content filter ────────────────────────────────────────────────────────
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
    text = (item.get('title', '') + ' ' + item.get('snippet', '')).lower()
    return any(kw in text for kw in _CODE_KEYWORDS)


# ── Keyword extraction for subsystem feeds ─────────────────────────────────────
_STOPWORDS = {
    'the','a','an','is','are','was','were','be','been','being','have','has','had',
    'do','does','did','will','would','could','should','may','might','can','shall',
    'to','of','in','on','at','by','for','with','from','that','this','these','those',
    'it','its','which','who','what','when','where','how','if','and','or','but','not',
    'no','as','so','than','then','also','into','through','over','between','out','up',
    'about','such','more','most','some','any','all','each','both','very','just','only',
    'other','after','before','first','last','much','many','well','even','still','way',
    'while','here','there','their','they','them','he','she','we','you','i','me','my',
    'our','your','his','her','its','been','being','make','made','new','used','use',
    'two','one','three','four','five','however','although','because','since','though',
}

def _keywords_from_sentences(sentences: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for sent in sentences:
        words = re.findall(r'\b[a-zA-Z]{4,}\b', sent.lower())
        words = [w for w in words if w not in _STOPWORDS]
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        for i in range(len(words) - 1):
            bg = f'{words[i]} {words[i+1]}'
            if bg not in seen:
                seen.add(bg)
                result.append(bg)
    return result


def _fstatus_write(fetching: bool) -> None:
    try:
        data = {
            'fetching':   fetching,
            'started_at': datetime.now(tz=timezone.utc).isoformat() if fetching else None,
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
        self._stats         = self._load_stats()
        self._last_checkin  = 0.0
        self._thread: threading.Thread | None = None
        self._graph         = None
        self._field         = None
        self._dynamics      = None
        self._known_urls: set = set()
        self._feed_lock     = threading.Lock()
        self._perf_lock     = threading.Lock()
        self._perf_samples: list[dict] = []
        self._subsys_buf:  list[str]   = []
        self._subsys_lock  = threading.Lock()
        self._feed_dirty   = 0
        try:
            existing = _FEED_PATH.read_text(encoding='utf-8').splitlines() if _FEED_PATH.exists() else []
            self._feed_buf: deque[str] = deque(existing[-FEED_MAX:], maxlen=FEED_MAX)
        except Exception:
            self._feed_buf = deque(maxlen=FEED_MAX)

    def _ensure_graph(self) -> None:
        if self._graph is None:
            try:
                from graph import SemanticGraph
                self._graph = SemanticGraph()
            except Exception:
                pass

    def _ensure_field(self) -> None:
        if self._field is None:
            try:
                from jam_field import JamField
                self._field = JamField.get()
            except Exception:
                pass
        if self._dynamics is None:
            try:
                from field_dynamics import FieldDynamics
                self._dynamics = FieldDynamics.get()
            except Exception:
                pass

    # ── Persistence ───────────────────────────────────────────────────────────

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
        try:
            with self._feed_lock:
                if self._feed_dirty > 0:
                    _FEED_PATH.write_text('\n'.join(self._feed_buf), encoding='utf-8')
                    self._feed_dirty = 0
        except Exception:
            pass

    # ── Pause ─────────────────────────────────────────────────────────────────

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

    # ── Subsystem buffer ──────────────────────────────────────────────────────

    def _subsys_queue(self, texts: list[str]) -> None:
        with self._subsys_lock:
            self._subsys_buf.extend(texts)
            if len(self._subsys_buf) > 2000:
                self._subsys_buf = self._subsys_buf[-2000:]

    # ── Background workers ────────────────────────────────────────────────────

    def _regulator_worker(self) -> None:
        """Regulation tick every 60s — Layer 4."""
        time.sleep(60)
        while True:
            try:
                if self._field is not None:
                    from regulation import Regulation
                    Regulation.get().tick(self._field)
            except Exception as e:
                log.debug('regulation error: %s', e)
            time.sleep(60)

    def _subsys_worker(self) -> None:
        """
        Background subsystems every 30s — Layer 5 only.
        Contradiction, world model, episodes, prediction.
        """
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

            _s(lambda: __import__('contradiction').ContradictionRegistry.get().observe(texts))
            _s(lambda: __import__('world_model').WorldModel.get().infer_from_context(texts))
            try:
                from episodes  import EpisodeStore
                from predictor import Predictor
                EpisodeStore.get().record(texts, ambiguity=0.0, region=None)
                Predictor.get().pre_activate(texts, None)
            except Exception:
                pass

    # ── Process one article ───────────────────────────────────────────────────

    def _process(self, item: dict) -> dict:
        from detector import detect_and_log
        _T = time.perf_counter

        entry = {
            'ts':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            'topic':    item.get('topic', ''),
            'title':    item.get('title', '')[:80],
            'source':   item.get('source', ''),
            'url':      item.get('url', ''),
            'concepts': 0,
            'sentences': 0,
            'status':   'ok',
        }

        # Signal live bar — article starting
        _cr = _DATA / 'currently_reading.json'
        try:
            _cr.write_text(json.dumps({
                'title':  item.get('title', '')[:160],
                'source': item.get('source', ''),
                'ts':     datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            }), encoding='utf-8')
        except Exception:
            pass

        # ── Fetch ──────────────────────────────────────────────────────────────
        t0 = _T()
        try:
            sentences = fetch_content(item)
        except Exception:
            sentences = _sentences(item.get('snippet', ''), min_len=20)
        t_fetch = _T() - t0

        if len(sentences) < _lcfg('min_sentences', MIN_SENTENCES):
            entry['status'] = 'skip:short'
            return entry

        # ── Batch embed ────────────────────────────────────────────────────────
        from extractor import extract_batch
        t1 = _T()
        batch = extract_batch(sentences)
        t_extract = _T() - t1

        # ── Graph update + JAM field ingest ───────────────────────────────────
        all_concepts: list = []
        all_node_ids: list[str] = []
        t_graph = 0.0
        n_total = len(sentences)

        for idx, concepts in enumerate(batch):
            # Update live bar per sentence
            try:
                _cr.write_text(json.dumps({
                    'title':    item.get('title', '')[:160],
                    'source':   item.get('source', ''),
                    'sentence': sentences[idx][:120] if idx < len(sentences) else '',
                    'progress': f'{idx + 1}/{n_total}',
                    'ts':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
                }), encoding='utf-8')
            except Exception:
                pass

            if not concepts:
                continue

            if self._graph:
                t1 = _T()
                node_ids = self._graph.update(concepts)
                t_graph += _T() - t1
            else:
                node_ids = [''] * len(concepts)

            all_concepts.extend(concepts)
            all_node_ids.extend(node_ids)

        if not all_concepts:
            entry['status'] = 'skip:no-concepts'
            return entry

        # ── JAM field ingest (Layer 2) ─────────────────────────────────────────
        if self._field is not None:
            self._field.ingest(all_concepts, all_node_ids)

        all_texts = [c.text for c in all_concepts]
        n_concepts = len(all_concepts)

        # ── Detect ambiguity — once per article ────────────────────────────────
        t1 = _T()
        sample = all_concepts[:30] if len(all_concepts) > 30 else all_concepts
        detect_and_log(item.get('title', ''), sample, graph=self._graph)
        t_detect = _T() - t1

        # ── Graph save ─────────────────────────────────────────────────────────
        t1 = _T()
        if self._graph:
            self._graph.save()
        t_save = _T() - t1

        # ── Queue sentences for Layer 5 subsystems ────────────────────────────
        self._subsys_queue(all_texts)
        t_total = _T() - t0

        # ── Stats ──────────────────────────────────────────────────────────────
        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + len(sentences)
        self._stats['total_concepts']  = self._stats.get('total_concepts', 0) + n_concepts

        entry['concepts']  = n_concepts
        entry['sentences'] = len(sentences)

        with self._perf_lock:
            self._perf_samples.append({
                'fetch':    round(t_fetch,   3),
                'extract':  round(t_extract, 3),
                'detect':   round(t_detect,  3),
                'graph':    round(t_graph,   3),
                'save':     round(t_save,    3),
                'subsys':   0.0,
                'total':    round(t_total,   3),
                'sentences': len(sentences),
                'concepts':  n_concepts,
                'source':    item.get('source', ''),
            })
            if len(self._perf_samples) > 50:
                self._perf_samples = self._perf_samples[-50:]

        return entry

    # ── One full cycle ────────────────────────────────────────────────────────

    def _write_perf_profile(self, cycle: dict) -> None:
        with self._perf_lock:
            samples = list(self._perf_samples)
        if not samples:
            return
        keys = ('fetch', 'extract', 'detect', 'graph', 'save', 'subsys', 'total')
        avg  = {k: round(sum(s.get(k, 0) for s in samples) / len(samples), 3) for k in keys}
        tot  = avg['total'] or 1
        pct  = {k: round(avg[k] / tot * 100, 1) for k in keys if k != 'total'}
        try:
            with open(self._PERF_PATH, 'w', encoding='utf-8') as f:
                json.dump({
                    'updated':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
                    'n_samples':     len(samples),
                    'process_avg_s': avg,
                    'process_pct':   pct,
                    'cycle':         cycle,
                }, f, indent=2)
        except Exception:
            pass

    def _cycle(self) -> None:
        self._ensure_graph()
        self._ensure_field()

        n_workers        = _lcfg('n_workers',        N_WORKERS)
        topics_per_cycle = _lcfg('topics_per_cycle', TOPICS_PER_CYCLE)
        fetch_timeout    = _lcfg('fetch_timeout',    FETCH_TIMEOUT)
        search_timeout   = _lcfg('search_timeout',   SEARCH_TIMEOUT)

        all_sources  = list(SOURCES.keys())
        topics       = random.sample(TOPICS, min(topics_per_cycle, len(TOPICS)))
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

        # ── Search phase ───────────────────────────────────────────────────────
        tc0 = time.perf_counter()
        _search_pool = ThreadPoolExecutor(max_workers=n_workers)
        try:
            for fut in as_completed(
                [_search_pool.submit(_search, t) for t in topics],
                timeout=search_timeout * 2
            ):
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

        # ── Process phase ──────────────────────────────────────────────────────
        n_ok = 0
        tp0  = time.perf_counter()
        _process_pool = ThreadPoolExecutor(max_workers=n_workers)
        futs = {_process_pool.submit(self._process, item): item
                for item in items_to_process}
        try:
            for fut in as_completed(futs, timeout=fetch_timeout * 3):
                if self.is_paused:
                    break
                try:
                    e = fut.result()
                    if e['status'] == 'ok':
                        n_ok += 1
                        self._feed_append(e)
                        log.info('learned: %s [%s] +%d concepts',
                                 e['title'], e['source'], e['concepts'])
                except Exception:
                    pass
        except Exception:
            log.debug('process phase timed out after %ds', fetch_timeout * 3)
        finally:
            _process_pool.shutdown(wait=False, cancel_futures=True)
        t_process = time.perf_counter() - tp0

        _fstatus_write(False)

        # ── Field dynamics pass (Layer 3) ──────────────────────────────────────
        if self._field and self._dynamics and self._graph:
            try:
                self._dynamics.propagate(self._field, self._graph)
            except Exception as e:
                log.debug('dynamics error: %s', e)

        # ── Field decay ────────────────────────────────────────────────────────
        if self._field:
            try:
                self._field.decay()
            except Exception as e:
                log.debug('field decay error: %s', e)

        # ── Save stats (once per cycle, not per article) ───────────────────────
        self._save_stats()

        # ── Growth log ────────────────────────────────────────────────────────
        if self._graph:
            try:
                line = json.dumps({
                    'ts':    datetime.now(tz=timezone.utc).isoformat(timespec='minutes'),
                    'nodes': self._graph.node_count,
                    'edges': self._graph.edge_count,
                })
                with open(_DATA / 'growth_log.jsonl', 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except Exception:
                pass

        # ── Periodic abstractor + worldview (every 3h) ────────────────────────
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

        # ── Flush feed ─────────────────────────────────────────────────────────
        self._feed_flush()

        # ── Perf profile ───────────────────────────────────────────────────────
        self._write_perf_profile({
            't_search_s':  round(t_search,  2),
            't_process_s': round(t_process, 2),
            't_ticks_s':   0.0,
            't_total_s':   round(t_search + t_process, 2),
            'items_found': len(items_to_process),
            'items_ok':    n_ok,
            'workers':     n_workers,
        })

    # ── Start ──────────────────────────────────────────────────────────────────

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

        def _watchdog():
            """Restart learner thread if it dies unexpectedly."""
            time.sleep(120)
            while True:
                if not self._thread.is_alive():
                    log.warning('learner thread died — restarting')
                    self._thread = threading.Thread(
                        target=_worker, daemon=True, name='learner')
                    self._thread.start()
                time.sleep(60)

        self._thread = threading.Thread(target=_worker, daemon=True, name='learner')
        self._thread.start()
        threading.Thread(target=self._subsys_worker,   daemon=True, name='subsys').start()
        threading.Thread(target=self._regulator_worker, daemon=True, name='regulator').start()
        threading.Thread(target=_watchdog,              daemon=True, name='watchdog').start()
