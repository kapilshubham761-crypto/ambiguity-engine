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

from sources   import SOURCES, _flesch_score, fetch_content, _sentences
from curriculum import CURRICULUM, STAGE_CONFIG
from logger    import get_logger

log = get_logger('learner')

# ── Config ────────────────────────────────────────────────────────────────────
CYCLE_TIME       = 10
N_WORKERS        = 4
TOPICS_PER_CYCLE = 6
MIN_SENTENCES    = 3
FETCH_TIMEOUT    = 18
SEARCH_TIMEOUT   = 7
CHECKIN_EVERY    = 3 * 3600
FEED_MAX         = 200

# ── Paths ─────────────────────────────────────────────────────────────────────
_STATS_PATH  = _DATA / 'learner_stats.json'
_FEED_PATH   = _DATA / 'live_feed.jsonl'
_STAGE_PATH  = _DATA / 'discovery_stage.json'
_PREFS_PATH  = _DATA / 'search_prefs.json'
_PAUSED_PATH = _DATA / 'paused.txt'
_FSTATUS     = _DATA / 'fetch_status.json'

REGIONS = {
    'global':      {'label': 'Global',       'terms': []},
    'mohali':      {'label': 'Mohali',        'terms': ['Mohali', 'Punjab India', 'Chandigarh']},
    'n_america':   {'label': 'N. America',   'terms': ['North America', 'United States', 'Canada']},
    'w_europe':    {'label': 'W. Europe',    'terms': ['Western Europe', 'UK', 'France', 'Germany']},
    'e_europe':    {'label': 'E. Europe',    'terms': ['Eastern Europe', 'Russia', 'Poland']},
    'n_asia':      {'label': 'N./C. Asia',   'terms': ['Central Asia', 'Siberia', 'Kazakhstan']},
    'c_america':   {'label': 'C. America',   'terms': ['Latin America', 'Mexico', 'Caribbean']},
    'n_africa_me': {'label': 'N. Africa/ME', 'terms': ['North Africa', 'Middle East']},
    'e_asia':      {'label': 'E. Asia',      'terms': ['China', 'Japan', 'Korea']},
    'india':       {'label': 'India',         'terms': ['India', 'South Asia', 'Hindi']},
    's_america':   {'label': 'S. America',   'terms': ['South America', 'Brazil', 'Argentina']},
    'sub_africa':  {'label': 'Sub-Saharan',  'terms': ['Sub-Saharan Africa', 'West Africa']},
    'oceania':     {'label': 'Oceania',       'terms': ['Australia', 'New Zealand', 'Pacific']},
}

def load_prefs() -> dict:
    try:
        return json.loads(_PREFS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'region': 'global'}

def save_prefs(p: dict) -> None:
    _PREFS_PATH.write_text(json.dumps(p), encoding='utf-8')


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

    # ── persistence ───────────────────────────────────────────────────────────

    def _load_stats(self) -> dict:
        try:
            return json.loads(_STATS_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {'total_accepted': 0, 'total_concepts': 0, 'total_sentences': 0}

    def _save_stats(self) -> None:
        tmp = _STATS_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._stats, indent=2), encoding='utf-8')
        tmp.replace(_STATS_PATH)

    def _feed_append(self, entry: dict) -> None:
        try:
            existing = _FEED_PATH.read_text(encoding='utf-8').splitlines() if _FEED_PATH.exists() else []
            existing.append(json.dumps(entry, ensure_ascii=False))
            _FEED_PATH.write_text('\n'.join(existing[-FEED_MAX:]), encoding='utf-8')
        except Exception:
            pass

    def _load_last_checkin(self) -> float:
        try:
            from assessor import load_cards
            cards = load_cards()
            if cards:
                dt = datetime.fromisoformat(cards[-1]['timestamp'])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        except Exception:
            pass
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

    # ── stage ─────────────────────────────────────────────────────────────────

    def current_stage(self) -> int:
        try:
            return int(json.loads(_STAGE_PATH.read_text(encoding='utf-8')).get('stage', 0))
        except Exception:
            return 0

    def set_stage(self, idx: int) -> None:
        idx = max(0, min(idx, len(CURRICULUM) - 1))
        _STAGE_PATH.write_text(json.dumps({'stage': idx}), encoding='utf-8')

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── process one search result ─────────────────────────────────────────────

    def _process(self, item: dict, stage: int) -> dict:
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

        if len(sentences) < MIN_SENTENCES:
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

        self._stats['total_accepted']  = self._stats.get('total_accepted', 0) + 1
        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + len(sentences)
        self._stats['total_concepts']  = self._stats.get('total_concepts', 0) + n_concepts
        self._save_stats()

        entry['concepts']  = n_concepts
        entry['sentences'] = len(sentences)
        return entry

    # ── one full cycle ────────────────────────────────────────────────────────

    def _cycle(self) -> None:
        stage     = self.current_stage()
        cfg       = STAGE_CONFIG[min(stage, len(STAGE_CONFIG) - 1)]
        min_read  = cfg['min_readability']
        sources   = [s for s in cfg['sources'] if s != 'web'] or cfg['sources']
        modifiers = cfg.get('modifiers', [])

        prefs        = load_prefs()
        region_key   = prefs.get('region', 'global')
        region_terms = REGIONS.get(region_key, REGIONS['global'])['terms']

        topics = list(CURRICULUM[stage]['topics'])
        random.shuffle(topics)
        topics = topics[:TOPICS_PER_CYCLE]

        def _search(topic):
            parts = [topic]
            if modifiers:   parts.append(random.choice(modifiers))
            if region_terms: parts.append(random.choice(region_terms))
            query = ' '.join(parts)
            src = random.choice(sources)
            try:
                search_fn, _ = SOURCES[src]
                results = search_fn(query, max_results=3)
                for r in results:
                    r['topic']  = topic
                    r['source'] = src
                return results
            except Exception:
                return []

        items_to_process = []
        _fstatus_write(True)

        with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            for fut in as_completed([pool.submit(_search, t) for t in topics],
                                    timeout=SEARCH_TIMEOUT * 2):
                try:
                    for item in fut.result():
                        url = item.get('url', '')
                        if url and url not in self._known_urls:
                            score = _flesch_score(item.get('snippet', ''))
                            if min_read <= 0 or score >= min_read:
                                items_to_process.append(item)
                                self._known_urls.add(url)
                except Exception:
                    pass

        if len(self._known_urls) > 5000:
            self._known_urls = set(list(self._known_urls)[-2000:])

        with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            futs = {pool.submit(self._process, item, stage): item
                    for item in items_to_process}
            for fut in as_completed(futs, timeout=FETCH_TIMEOUT * 3):
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

        # Check-in
        if time.time() - self._last_checkin >= CHECKIN_EVERY and self._graph:
            try:
                from assessor   import assess, save_card, load_cards, Assessment
                from abstractor import Abstractor
                from worldview  import Worldview
                from homework   import HomeworkTracker
                from meta_state import MetaState
                stage_now = self.current_stage()
                prev = None
                cards = load_cards()
                if cards:
                    try: prev = Assessment(**cards[-1])
                    except Exception: pass
                card = assess(self._graph, stage_now, prev)
                save_card(card)
                HomeworkTracker().generate(stage_now, self._graph, meta=MetaState.get())
                Abstractor.get().run()
                Worldview.get().update()
                self._last_checkin = time.time()
                log.info('check-in complete grade=%s', card.grade)
            except Exception as e:
                log.debug('check-in error: %s', e)

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

    # ── background thread ─────────────────────────────────────────────────────

    def start(self, graph=None) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._graph = graph

        def _worker():
            log.info('AutoLearner started (cycle=%ds workers=%d)', CYCLE_TIME, N_WORKERS)
            try:
                from assessor import ensure_birthdate
                ensure_birthdate()
            except Exception:
                pass
            while True:
                try:
                    if self.is_paused:
                        _fstatus_write(False)
                    else:
                        self._cycle()
                except Exception as e:
                    log.debug('cycle error: %s', e)
                    _fstatus_write(False)
                time.sleep(CYCLE_TIME)

        self._thread = threading.Thread(target=_worker, daemon=True, name='learner')
        self._thread.start()
