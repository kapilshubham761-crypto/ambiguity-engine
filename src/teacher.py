"""
Node [D] — Teacher (Orchestrator)
=============================================
High-level orchestrator that wires components together and runs the
24-hour background learning loop.

Single responsibility: decide *what* to learn next and *when* to assess.
Delegates all storage concerns to injected components (DIP).

Component dependencies (all via protocols, not concrete classes)
---------------------------------------------------------------
IQueue        ← queue_mgr.LessonQueue
IHomework     ← homework.HomeworkTracker
IGraph        ← graph.SemanticGraph    (passed at start-time)
assessor      ← Block I (called via check_in)

Public API
----------
Teacher             class   the orchestrator; hold one per process
load_prefs()        fn      read region + year filter preferences
save_prefs()        fn      write region + year filter preferences
REGIONS             dict    12-region spherical grid for query biasing
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sources import SOURCES, _flesch_score, fetch_content
from curriculum import CURRICULUM, STAGE_CONFIG
from protocols import IQueue, IHomework


# ======================================================================== #
# Paths                                                                     #
# ======================================================================== #

_ROOT       = os.path.join(os.path.dirname(__file__), '..')
_STATS_PATH = os.path.join(_ROOT, 'data', 'teacher_stats.json')
_STAGE_PATH = os.path.join(_ROOT, 'data', 'discovery_stage.json')
_PREFS_PATH = os.path.join(_ROOT, 'data', 'search_prefs.json')


# ======================================================================== #
# Constants                                                                 #
# ======================================================================== #

MIN_LESSONS       = 5
MAX_LESSONS       = 15
CHECK_EVERY       = 60        # seconds — background thread cycle
CHECKIN_EVERY     = 3 * 3600  # seconds — report card interval (3 hours)
MIN_SENTENCES     = 3
FETCH_TIMEOUT     = 20        # seconds — per lazy content fetch
SEARCH_TIMEOUT    = 8         # seconds — per source search call

GRAPH_WARN_NODES  = 50_000
GRAPH_LIMIT_NODES = 150_000

ALL_SOURCES = list(SOURCES.keys())


# ======================================================================== #
# World regions                                                             #
# Grid layout mirrors a spherical lat/lon map (3 rows × 4 columns).       #
# ======================================================================== #

REGIONS = {
    'global':      {'label': '🌐 Global',        'terms': []},
    'mohali':      {'label': '📍 Mohali',         'terms': ['Mohali', 'Punjab India', 'Chandigarh', 'Tricity']},
    # Northern band
    'n_america':   {'label': '🌎 N. America',    'terms': ['North America', 'United States', 'Canada']},
    'w_europe':    {'label': '🌍 W. Europe',     'terms': ['Western Europe', 'United Kingdom', 'France', 'Germany']},
    'e_europe':    {'label': '🌍 E. Europe',     'terms': ['Eastern Europe', 'Russia', 'Poland', 'Ukraine']},
    'n_asia':      {'label': '🌏 N./C. Asia',    'terms': ['Central Asia', 'Siberia', 'Kazakhstan']},
    # Middle band
    'c_america':   {'label': '🌎 C. America',    'terms': ['Latin America', 'Mexico', 'Caribbean', 'Central America']},
    'n_africa_me': {'label': '🌍 N. Africa/ME',  'terms': ['North Africa', 'Middle East', 'Arab world']},
    'e_asia':      {'label': '🌏 E. Asia',       'terms': ['China', 'Japan', 'Korea', 'East Asia']},
    'india':       {'label': '🌏 India',          'terms': ['India', 'South Asia', 'Hindi', 'Punjabi']},
    # Southern band
    's_america':   {'label': '🌎 S. America',    'terms': ['South America', 'Brazil', 'Argentina']},
    'sub_africa':  {'label': '🌍 Sub-Saharan',   'terms': ['Sub-Saharan Africa', 'West Africa', 'East Africa']},
    'oceania':     {'label': '🌏 Oceania',        'terms': ['Australia', 'New Zealand', 'Pacific Islands']},
}

_GRID = [
    ['mohali',   'n_america',   'w_europe',    'e_europe'],
    ['n_asia',   'c_america',   'n_africa_me', 'e_asia'],
    ['india',    's_america',   'sub_africa',  'oceania'],
]


# ======================================================================== #
# Region preferences — file-backed, shared across pages                    #
# ======================================================================== #

def load_prefs() -> dict:
    try:
        with open(_PREFS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'region': 'mohali', 'year_from': None, 'year_to': None}


def save_prefs(prefs: dict) -> None:
    os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
    with open(_PREFS_PATH, 'w', encoding='utf-8') as f:
        json.dump(prefs, f)


# ======================================================================== #
# Teacher                                                                   #
# Depends on IQueue and IHomework, not LessonQueue/HomeworkTracker.        #
# This satisfies the Dependency Inversion Principle.                        #
# ======================================================================== #

class Teacher:
    """
    Orchestrates the autonomous learning loop.

    Pass queue and homework via constructor for testability (DIP).
    Call with no arguments for the standard runtime configuration.
    """

    def __init__(self,
                 queue:    IQueue    | None = None,
                 homework: IHomework | None = None) -> None:
        # Late import to avoid circular dependency at module load
        from queue_mgr import LessonQueue
        from homework import HomeworkTracker

        os.makedirs(os.path.join(_ROOT, 'data'), exist_ok=True)

        self._q:  IQueue    = queue    or LessonQueue()
        self._hw: IHomework = homework or HomeworkTracker()

        self._thread: threading.Thread | None = None
        self._status       = 'idle'
        self._stats        = self._load_stats()
        self._last_checkin = self._load_last_checkin()
        self._paused       = self._load_paused()

        from assessor import ensure_birthdate
        ensure_birthdate()

    # -------------------------------------------------------------------- #
    # Stats persistence                                                      #
    # -------------------------------------------------------------------- #

    def _load_stats(self) -> dict:
        try:
            with open(_STATS_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {'total_accepted': 0, 'total_rejected': 0,
                    'total_sentences': 0, 'sessions': []}

    def _save_stats(self) -> None:
        with open(_STATS_PATH, 'w', encoding='utf-8') as f:
            json.dump(self._stats, f, ensure_ascii=False, indent=2)

    def _log_session(self, action, lesson, sentences, concepts) -> None:
        self._stats.setdefault('sessions', []).append({
            'action':      action,
            'title':       lesson.get('title', ''),
            'source':      lesson.get('source', ''),
            'topic':       lesson.get('topic', ''),
            'stage':       lesson.get('stage', 0),
            'stage_label': lesson.get('stage_label', ''),
            'sentences':   sentences,
            'concepts':    concepts,
            'ts':          datetime.now(tz=timezone.utc).isoformat(),
        })
        self._stats['sessions'] = self._stats['sessions'][-500:]

    # -------------------------------------------------------------------- #
    # Paused state                                                           #
    # -------------------------------------------------------------------- #

    def _load_paused(self) -> bool:
        try:
            return open(os.path.join(_ROOT, 'data', 'paused.txt')).read().strip() == '1'
        except Exception:
            return False

    def _save_paused(self) -> None:
        with open(os.path.join(_ROOT, 'data', 'paused.txt'), 'w') as f:
            f.write('1' if self._paused else '0')

    @property
    def is_paused(self) -> bool:
        self._paused = self._load_paused()
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._status = 'paused'
        self._save_paused()

    def resume(self) -> None:
        self._paused = False
        self._status = 'idle'
        self._save_paused()

    # -------------------------------------------------------------------- #
    # Auto-accept toggle                                                     #
    # -------------------------------------------------------------------- #

    def _load_auto_accept(self) -> bool:
        try:
            return open(os.path.join(_ROOT, 'data', 'auto_accept.txt')).read().strip() == '1'
        except Exception:
            return True

    def set_auto_accept(self, enabled: bool) -> None:
        with open(os.path.join(_ROOT, 'data', 'auto_accept.txt'), 'w') as f:
            f.write('1' if enabled else '0')

    # -------------------------------------------------------------------- #
    # Stage control                                                          #
    # -------------------------------------------------------------------- #

    def _current_stage(self) -> int:
        try:
            with open(_STAGE_PATH, encoding='utf-8') as f:
                return int(json.load(f).get('stage', 0))
        except Exception:
            return 0

    def set_stage(self, index: int) -> None:
        index = max(0, min(index, len(CURRICULUM) - 1))
        with open(_STAGE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'stage': index}, f)
        self._q.clear()

    def advance_stage(self) -> bool:
        stage = self._current_stage()
        if stage >= len(CURRICULUM) - 1:
            return False
        self.set_stage(stage + 1)
        return True

    # -------------------------------------------------------------------- #
    # Check-in timestamp (survives restarts via saved report cards)         #
    # -------------------------------------------------------------------- #

    def _load_last_checkin(self) -> float:
        try:
            from assessor import load_cards
            cards = load_cards()
            if cards:
                ts = cards[-1]['timestamp']
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        except Exception:
            pass
        return 0.0

    # -------------------------------------------------------------------- #
    # Growth log                                                             #
    # -------------------------------------------------------------------- #

    def _write_growth(self, graph) -> None:
        try:
            p = os.path.join(_ROOT, 'data', 'growth_log.jsonl')
            entry = {
                'ts':    datetime.now(tz=timezone.utc).isoformat(timespec='minutes'),
                'nodes': graph.node_count,
                'edges': graph.edge_count,
            }
            try:
                last = json.loads(open(p, encoding='utf-8').readlines()[-1])
                if last['nodes'] == entry['nodes']:
                    return
            except Exception:
                pass
            with open(p, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass

    # -------------------------------------------------------------------- #
    # Public read interface                                                  #
    # -------------------------------------------------------------------- #

    @property
    def queue(self) -> list[dict]:
        return self._q.list()

    @property
    def status(self) -> str:
        return self._status

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def homework(self) -> list[dict]:
        return self._hw.all()

    # -------------------------------------------------------------------- #
    # Lesson actions — accept / reject                                      #
    # -------------------------------------------------------------------- #

    def accept(self, lesson_id: str, graph) -> int:
        """
        Accept a lesson: lazy-fetch content, extract concepts, update graph.
        Returns the number of sentences fed into the graph.
        """
        from extractor import extract
        from detector import detect_and_log

        lesson = self._q.get(lesson_id)
        if lesson is None:
            return 0

        sentences = lesson.get('sentences', [])

        # Lazy fetch — full content is fetched on accept, not on search
        if not sentences and lesson.get('url'):
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(fetch_content, lesson.get('_item') or lesson)
                    sentences = fut.result(timeout=FETCH_TIMEOUT)
            except Exception:
                sentences = []
            if not sentences:
                from sources import _sentences as _sent
                sentences = _sent(lesson.get('preview', ''), min_len=20)

        if len(sentences) < MIN_SENTENCES:
            self._q.remove(lesson_id)
            return 0

        n_concepts = 0
        all_texts: list[str] = []
        for sent in sentences:
            concepts = extract(sent)
            if concepts:
                detect_and_log(sent, concepts, graph=graph)
                graph.update(concepts)
                n_concepts += len(concepts)
                all_texts.extend(c.text for c in concepts)

        if sentences:
            graph.save()
            try:
                from meta_state import MetaState
                MetaState.get().reinforce(all_texts)
            except Exception as _e:
                log.debug('meta_state update skipped: %s', _e)

        self._stats['total_accepted']  = self._stats.get('total_accepted', 0) + 1
        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + len(sentences)
        self._log_session('accept', lesson, len(sentences), n_concepts)
        self._save_stats()

        self._hw.tick(lesson.get('topic', ''))
        self._q.remove(lesson_id)
        return len(sentences)

    def reject(self, lesson_id: str) -> None:
        self._stats['total_rejected'] = self._stats.get('total_rejected', 0) + 1
        lesson = self._q.get(lesson_id)
        if lesson:
            self._log_session('reject', lesson, 0, 0)
        self._save_stats()
        self._q.remove(lesson_id)

    # -------------------------------------------------------------------- #
    # Check-in — periodic report card + homework refresh                   #
    # -------------------------------------------------------------------- #

    def check_in(self, graph) -> dict:
        """Run a full assessment, save the report card, and refresh homework."""
        from assessor import assess, save_card, load_cards, Assessment

        stage = self._current_stage()
        prev  = None
        cards = load_cards()
        if cards:
            try:
                prev = Assessment(**cards[-1])
            except Exception:
                pass

        card = assess(graph, stage, prev)
        save_card(card)

        self._hw.generate(stage, graph)

        self._last_checkin = time.time()
        return card.__dict__

    def next_checkin_in(self) -> str:
        if self._last_checkin == 0.0:
            return 'never run yet — will run shortly'
        remaining = CHECKIN_EVERY - (time.time() - self._last_checkin)
        if remaining <= 0:
            return 'due now'
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        return f"in {h}h {m}m"

    # -------------------------------------------------------------------- #
    # Refill — populate queue from discovery sources                        #
    # Fast path: search only.  Full content is fetched lazily in accept(). #
    # -------------------------------------------------------------------- #

    def _refill(self) -> int:
        existing_urls = self._q.known_urls()
        needed = MAX_LESSONS - self._q.size()

        if needed <= 0:
            return 0

        self._status = 'fetching lessons…'
        _fsp = os.path.join(_ROOT, 'data', 'fetch_status.json')
        try:
            with open(_fsp, 'w', encoding='utf-8') as f:
                json.dump({'fetching': True,
                           'started_at': datetime.now(tz=timezone.utc).isoformat(),
                           'needed': needed}, f)
        except Exception:
            pass

        added = 0
        try:
            stage        = self._current_stage()
            cfg          = STAGE_CONFIG[min(stage, len(STAGE_CONFIG) - 1)]
            preferred    = [s for s in cfg['sources'] if s != 'web']
            stage_sources = preferred or cfg['sources']
            min_read     = cfg['min_readability']
            modifiers    = cfg['modifiers']

            prefs        = load_prefs()
            region_key   = prefs.get('region', 'global')
            region_terms = REGIONS.get(region_key, REGIONS['global'])['terms']
            year_from    = prefs.get('year_from')
            year_to      = prefs.get('year_to')

            pending_topics = self._hw.pending_topics()
            all_topics     = pending_topics + list(CURRICULUM[stage]['topics'])
            random.shuffle(all_topics)

            # Frame each topic with region/year/modifier suffixes
            framed_topics = []
            for topic in all_topics:
                parts = [topic]
                if modifiers:
                    parts.append(random.choice(modifiers))
                if region_terms:
                    parts.append(random.choice(region_terms))
                if year_from and year_to:
                    parts.append(f"{random.randint(year_from, year_to)}")
                elif year_from:
                    parts.append(str(year_from))
                framed_topics.append(' '.join(parts))

            for framed_topic, raw_topic in zip(framed_topics, all_topics):
                if added >= needed or self._load_paused():
                    break

                sources = stage_sources[:2]
                all_results: list[dict] = []

                def _search_one(src, q=framed_topic):
                    try:
                        search_fn, _ = SOURCES[src]
                        return search_fn(q, max_results=4)
                    except Exception:
                        return []

                with ThreadPoolExecutor(max_workers=len(sources)) as pool:
                    futs = {pool.submit(_search_one, src): src for src in sources}
                    for fut in futs:
                        try:
                            all_results.extend(fut.result(timeout=SEARCH_TIMEOUT))
                        except Exception:
                            pass

                for item in all_results:
                    if added >= needed or self._load_paused():
                        break
                    if item.get('_error') or not item.get('url'):
                        continue
                    if item['url'] in existing_urls:
                        continue
                    if min_read > 0 and _flesch_score(item.get('snippet', '')) < min_read:
                        continue

                    is_hw  = raw_topic in pending_topics
                    lesson = {
                        'id':             str(uuid.uuid4()),
                        'title':          item.get('title', raw_topic),
                        'url':            item['url'],
                        'source':         item.get('source', 'web'),
                        'topic':          raw_topic,
                        'stage':          stage,
                        'stage_label':    CURRICULUM[stage]['label'],
                        'sentences':      [],
                        'preview':        item.get('snippet', '')[:300],
                        'sentence_count': 0,
                        'fetched_at':     datetime.now(tz=timezone.utc).isoformat(),
                        'is_homework':    is_hw,
                        '_item':          item,
                    }
                    self._q.add(lesson)
                    existing_urls.add(item['url'])
                    added += 1

        finally:
            self._status = 'idle'
            try:
                with open(_fsp, 'w', encoding='utf-8') as f:
                    json.dump({'fetching': False, 'started_at': None, 'needed': 0}, f)
            except Exception:
                pass

        return added

    # -------------------------------------------------------------------- #
    # Background thread                                                      #
    #   1. Sync paused flag from file                                       #
    #   2. Gate on graph size                                               #
    #   3. Refill queue if low                                              #
    #   4. Auto-accept all queued lessons (if enabled)                      #
    #   5. Write growth snapshot                                            #
    #   6. Run check-in if due                                              #
    # -------------------------------------------------------------------- #

    def start(self, graph=None) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _worker():
            while True:
                try:
                    self._paused = self._load_paused()

                    if self._paused:
                        self._status = 'paused'
                        try:
                            with open(os.path.join(_ROOT, 'data', 'fetch_status.json'), 'w') as f:
                                json.dump({'fetching': False, 'started_at': None, 'needed': 0}, f)
                        except Exception:
                            pass

                    else:
                        if graph and graph.node_count >= GRAPH_LIMIT_NODES:
                            self._status = 'graph full — pruning recommended'
                            time.sleep(CHECK_EVERY)
                            continue

                        if self._q.size() < MIN_LESSONS:
                            self._refill()

                        if graph is not None and self._load_auto_accept():
                            self._status = 'auto-accepting…'
                            for lesson in self._q.list():
                                if self._load_paused():
                                    break
                                try:
                                    self.accept(lesson['id'], graph)
                                except Exception:
                                    pass
                            self._status = 'idle'

                        if graph is not None:
                            self._write_growth(graph)

                        if graph is not None:
                            if time.time() - self._last_checkin >= CHECKIN_EVERY:
                                self._status = 'running check-in…'
                                self.check_in(graph)
                                self._status = 'idle'

                except Exception:
                    self._status = 'idle'

                time.sleep(CHECK_EVERY)

        self._thread = threading.Thread(target=_worker, daemon=True, name='teacher')
        self._thread.start()
