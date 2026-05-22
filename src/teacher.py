"""
Teacher — 24-hour autonomous teaching pipeline with homework and report cards.

Every CHECK_EVERY seconds: refill lessons if queue is low.
Every CHECKIN_EVERY seconds: run a full assessment, issue homework, save report card.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from datetime import datetime, date, timezone

from discover import search_sources, fetch_content, SOURCES, _flesch_score
from auto_discover import CURRICULUM, STAGE_CONFIG

_ROOT         = os.path.join(os.path.dirname(__file__), '..')
_LESSON_PATH  = os.path.join(_ROOT, 'data', 'teacher_queue.json')
_STATS_PATH   = os.path.join(_ROOT, 'data', 'teacher_stats.json')
_STAGE_PATH   = os.path.join(_ROOT, 'data', 'discovery_stage.json')
_HW_PATH      = os.path.join(_ROOT, 'data', 'homework.json')
_PREFS_PATH   = os.path.join(_ROOT, 'data', 'search_prefs.json')

MIN_LESSONS   = 5
MAX_LESSONS   = 10
CHECK_EVERY   = 120          # refill check interval (seconds)
CHECKIN_EVERY = 3 * 3600     # report card interval (3 hours)
MIN_SENTENCES = 3

ALL_SOURCES   = list(SOURCES.keys())

# --------------------------------------------------------------------------- #
# World regions — spherical grid (lat band × lon sector)                       #
# Each entry: display label + search terms to append to queries                #
# --------------------------------------------------------------------------- #
REGIONS = {
    'global':      {'label': '🌐 Global',        'terms': []},
    # Northern band
    'n_america':   {'label': '🌎 N. America',    'terms': ['North America', 'United States', 'Canada']},
    'w_europe':    {'label': '🌍 W. Europe',     'terms': ['Western Europe', 'United Kingdom', 'France', 'Germany']},
    'e_europe':    {'label': '🌍 E. Europe',     'terms': ['Eastern Europe', 'Russia', 'Poland', 'Ukraine']},
    'n_asia':      {'label': '🌏 N./C. Asia',    'terms': ['Central Asia', 'Siberia', 'Kazakhstan']},
    # Middle band
    'c_america':   {'label': '🌎 C. America',    'terms': ['Latin America', 'Mexico', 'Caribbean', 'Central America']},
    'n_africa_me': {'label': '🌍 N. Africa/ME',  'terms': ['North Africa', 'Middle East', 'Arab world']},
    'e_asia':      {'label': '🌏 E. Asia',       'terms': ['China', 'Japan', 'Korea', 'East Asia']},
    'se_asia':     {'label': '🌏 S.E. Asia',     'terms': ['Southeast Asia', 'India', 'Thailand', 'Vietnam']},
    # Southern band
    's_america':   {'label': '🌎 S. America',    'terms': ['South America', 'Brazil', 'Argentina']},
    'sub_africa':  {'label': '🌍 Sub-Saharan',   'terms': ['Sub-Saharan Africa', 'West Africa', 'East Africa']},
    'oceania':     {'label': '🌏 Oceania',        'terms': ['Australia', 'New Zealand', 'Pacific Islands']},
}

_GRID = [
    # Each row: list of region keys (left→right = west→east)
    ['n_america',  'w_europe',    'e_europe',  'n_asia'],
    ['c_america',  'n_africa_me', 'e_asia',    'se_asia'],
    ['s_america',  'sub_africa',  'oceania',   None],
]


def load_prefs() -> dict:
    try:
        with open(_PREFS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'region': 'global', 'year_from': None, 'year_to': None}


def save_prefs(prefs: dict) -> None:
    os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
    with open(_PREFS_PATH, 'w', encoding='utf-8') as f:
        json.dump(prefs, f)


class Teacher:
    """Singleton — held by st.cache_resource."""

    def __init__(self):
        os.makedirs(os.path.join(_ROOT, 'data'), exist_ok=True)
        self._lock         = threading.Lock()
        self._queue        = self._load_queue()
        self._thread: threading.Thread | None = None
        self._status       = 'idle'
        self._stats        = self._load_stats()
        self._last_checkin = self._load_last_checkin()
        self._homework     = self._load_homework()

        from report_card import ensure_birthdate
        ensure_birthdate()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load_queue(self) -> list[dict]:
        try:
            with open(_LESSON_PATH, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_queue(self) -> None:
        slim = [{k: v for k, v in l.items() if k != 'sentences'} for l in self._queue]
        with open(_LESSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)

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

    def _load_homework(self) -> list[dict]:
        try:
            with open(_HW_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def _load_last_checkin(self) -> float:
        """Restore last check-in time from saved report cards so countdown survives restart."""
        try:
            from report_card import load_cards
            cards = load_cards()
            if cards:
                from datetime import datetime, timezone
                ts = cards[-1]['timestamp']
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        except Exception:
            pass
        return 0.0

    def _save_homework(self) -> None:
        with open(_HW_PATH, 'w', encoding='utf-8') as f:
            json.dump(self._homework, f, ensure_ascii=False, indent=2)

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
        with self._lock:
            self._queue = []
            self._save_queue()

    def advance_stage(self) -> bool:
        stage = self._current_stage()
        if stage >= len(CURRICULUM) - 1:
            return False
        self.set_stage(stage + 1)
        return True

    # ------------------------------------------------------------------ #
    # Public interface                                                      #
    # ------------------------------------------------------------------ #

    @property
    def queue(self) -> list[dict]:
        with self._lock:
            return list(self._queue)

    @property
    def status(self) -> str:
        return self._status

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def homework(self) -> list[dict]:
        return list(self._homework)

    def accept(self, lesson_id: str, graph) -> int:
        from extractor import extract
        from detector import detect_and_log

        with self._lock:
            lesson = next((l for l in self._queue if l['id'] == lesson_id), None)
            if lesson is None:
                return 0
            sentences = lesson.get('sentences', [])

        n_concepts = 0
        for sent in sentences:
            concepts = extract(sent)
            if concepts:
                detect_and_log(sent, concepts, graph=graph)
                graph.update(concepts)
                n_concepts += len(concepts)

        if sentences:
            graph.save()

        self._stats['total_accepted']  = self._stats.get('total_accepted', 0) + 1
        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + len(sentences)
        self._log_session('accept', lesson, len(sentences), n_concepts)
        self._save_stats()

        # Mark related homework as partially done
        self._tick_homework(lesson.get('topic', ''))

        self._remove(lesson_id)
        return len(sentences)

    def reject(self, lesson_id: str) -> None:
        self._stats['total_rejected'] = self._stats.get('total_rejected', 0) + 1
        with self._lock:
            lesson = next((l for l in self._queue if l['id'] == lesson_id), None)
        if lesson:
            self._log_session('reject', lesson, 0, 0)
        self._save_stats()
        self._remove(lesson_id)

    def _remove(self, lesson_id: str) -> None:
        with self._lock:
            self._queue = [l for l in self._queue if l['id'] != lesson_id]
            self._save_queue()

    def _log_session(self, action, lesson, sentences, concepts) -> None:
        self._stats.setdefault('sessions', []).append({
            'action':    action,
            'title':     lesson.get('title', ''),
            'source':    lesson.get('source', ''),
            'topic':     lesson.get('topic', ''),
            'stage':     lesson.get('stage', 0),
            'sentences': sentences,
            'concepts':  concepts,
            'ts':        datetime.now(tz=timezone.utc).isoformat(),
        })
        self._stats['sessions'] = self._stats['sessions'][-500:]

    # ------------------------------------------------------------------ #
    # Homework                                                             #
    # ------------------------------------------------------------------ #

    def _generate_homework(self, graph) -> list[dict]:
        """Identify weak spots in the graph and assign targeted topics."""
        stage   = self._current_stage()
        topics  = list(CURRICULUM[stage]['topics'])

        # Find known concept texts
        known = {data['text'].lower() for _, data in graph._g.nodes(data=True)}

        assignments = []
        for topic in topics:
            # Check how many words in this topic appear in the graph
            words       = set(topic.lower().split())
            coverage    = len(words & known) / max(len(words), 1)
            status      = 'done' if coverage >= 0.5 else 'pending'
            assignments.append({
                'id':       str(uuid.uuid4()),
                'topic':    topic,
                'stage':    stage,
                'coverage': round(coverage, 2),
                'status':   status,
                'assigned': datetime.now(tz=timezone.utc).isoformat(),
                'due':      'next check-in',
            })

        # Sort by coverage ascending — least known first
        assignments.sort(key=lambda a: a['coverage'])
        return assignments[:15]   # top 15 gaps

    def _tick_homework(self, topic: str) -> None:
        """Mark a homework item as done when its topic is accepted."""
        for hw in self._homework:
            if hw.get('topic', '').lower() == topic.lower():
                hw['status'] = 'done'
        self._save_homework()

    # ------------------------------------------------------------------ #
    # Check-in (periodic report card)                                      #
    # ------------------------------------------------------------------ #

    def check_in(self, graph) -> dict:
        """Run a full assessment, save report card, refresh homework."""
        from report_card import assess, save_card, load_cards

        stage = self._current_stage()
        cards = load_cards()
        prev  = None
        if cards:
            from report_card import Assessment
            try:
                prev = Assessment(**cards[-1])
            except Exception:
                pass

        card = assess(graph, stage, prev)
        save_card(card)

        # Refresh homework based on current graph state
        self._homework = self._generate_homework(graph)
        self._save_homework()

        self._last_checkin = time.time()
        return card.__dict__

    def next_checkin_in(self) -> str:
        """Human-readable time until next check-in."""
        if self._last_checkin == 0.0:
            return 'never run yet — will run shortly'
        remaining = CHECKIN_EVERY - (time.time() - self._last_checkin)
        if remaining <= 0:
            return 'due now'
        h = int(remaining // 3600)
        m = int((remaining % 3600) // 60)
        return f"in {h}h {m}m"

    # ------------------------------------------------------------------ #
    # Refill lessons                                                       #
    # ------------------------------------------------------------------ #

    def _refill(self) -> int:
        with self._lock:
            existing_urls = {l['url'] for l in self._queue if l.get('url')}
            needed = MAX_LESSONS - len(self._queue)

        if needed <= 0:
            return 0

        self._status = 'fetching lessons…'
        stage  = self._current_stage()
        cfg    = STAGE_CONFIG[min(stage, len(STAGE_CONFIG) - 1)]
        stage_sources = cfg['sources']
        min_read      = cfg['min_readability']
        modifiers     = cfg['modifiers']

        # Load location + time prefs
        prefs      = load_prefs()
        region_key = prefs.get('region', 'global')
        region_terms = REGIONS.get(region_key, REGIONS['global'])['terms']
        year_from  = prefs.get('year_from')
        year_to    = prefs.get('year_to')

        # Prioritise pending homework topics
        pending    = [hw['topic'] for hw in self._homework if hw.get('status') == 'pending']
        all_topics = pending + list(CURRICULUM[stage]['topics'])
        random.shuffle(all_topics)

        # Apply age-appropriate query framing + location + year
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

        added = 0
        for framed_topic, raw_topic in zip(framed_topics, all_topics):
            if added >= needed:
                break
            sources = stage_sources[:min(2, len(stage_sources))]
            try:
                results = search_sources(framed_topic, sources, max_per_source=3)
            except Exception:
                continue

            for item in results:
                if added >= needed:
                    break
                if item.get('_error') or not item.get('url'):
                    continue
                if item['url'] in existing_urls:
                    continue
                # Readability pre-filter on snippet
                if min_read > 0:
                    if _flesch_score(item.get('snippet', '')) < min_read:
                        continue
                try:
                    sentences = fetch_content(item)
                except Exception:
                    continue
                if len(sentences) < MIN_SENTENCES:
                    continue

                is_hw = raw_topic in pending
                lesson = {
                    'id':             str(uuid.uuid4()),
                    'title':          item.get('title', raw_topic),
                    'url':            item['url'],
                    'source':         item.get('source', 'web'),
                    'topic':          raw_topic,
                    'stage':          stage,
                    'stage_label':    CURRICULUM[stage]['label'],
                    'sentences':      sentences,
                    'preview':        ' '.join(sentences[:3]),
                    'sentence_count': len(sentences),
                    'fetched_at':     datetime.now(tz=timezone.utc).isoformat(),
                    'is_homework':    is_hw,
                }

                with self._lock:
                    self._queue.append(lesson)
                    self._save_queue()

                existing_urls.add(item['url'])
                added += 1

        self._status = 'idle'
        return added

    # ------------------------------------------------------------------ #
    # Background thread                                                    #
    # ------------------------------------------------------------------ #

    def start(self, graph=None) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _worker():
            while True:
                try:
                    # Refill if low
                    with self._lock:
                        size = len(self._queue)
                    if size < MIN_LESSONS:
                        self._refill()

                    # Check-in if due
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
