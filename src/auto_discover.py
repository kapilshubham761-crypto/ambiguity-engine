"""
Autonomous discovery — background thread that keeps a persistent queue
of search results topped up to MIN_QUEUE items at all times.

Content follows a developmental curriculum: starts with what a toddler
encounters (colours, animals, simple stories) and advances stage by
stage toward graduate-level abstraction.  The current stage is persisted
in data/discovery_stage.json so progress survives restarts.

Data (CURRICULUM, STAGE_CONFIG) imported from Block A (curriculum.py).
Search + fetch functions imported from Block B (sources.py).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime, timezone

from sources import search_sources, SOURCES, _flesch_score, search_gutenberg
from curriculum import CURRICULUM, STAGE_CONFIG

_ROOT        = os.path.join(os.path.dirname(__file__), '..')
QUEUE_PATH   = os.path.join(_ROOT, 'data', 'discovery_queue.json')
STAGE_PATH   = os.path.join(_ROOT, 'data', 'discovery_stage.json')
MIN_QUEUE    = 10
MAX_QUEUE    = 20
CHECK_EVERY  = 180   # seconds between refill checks

ALL_SOURCES  = list(SOURCES.keys())


# ======================================================================== #
# AutoDiscovery                                                             #
# ======================================================================== #

class AutoDiscovery:
    """Singleton — one per Streamlit process (held by st.cache_resource)."""

    def __init__(self):
        os.makedirs(os.path.join(_ROOT, 'data'), exist_ok=True)
        self._lock   = threading.Lock()
        self._queue  = self._load_queue()
        self._stage  = self._load_stage()
        self._thread: threading.Thread | None = None
        self._last_refill: str = ''
        self._status: str = 'idle'

    # -------------------------------------------------------------------- #
    # Persistence                                                            #
    # -------------------------------------------------------------------- #

    def _load_queue(self) -> list[dict]:
        try:
            with open(QUEUE_PATH, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_queue(self) -> None:
        with open(QUEUE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self._queue, f, ensure_ascii=False, indent=2)

    def _load_stage(self) -> int:
        try:
            with open(STAGE_PATH, encoding='utf-8') as f:
                return int(json.load(f).get('stage', 0))
        except Exception:
            return 0

    def _save_stage(self) -> None:
        with open(STAGE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'stage': self._stage}, f)

    # -------------------------------------------------------------------- #
    # Stage control                                                          #
    # -------------------------------------------------------------------- #

    @property
    def stage(self) -> int:
        return self._stage

    @property
    def stage_info(self) -> dict:
        return CURRICULUM[self._stage]

    @property
    def stage_count(self) -> int:
        return len(CURRICULUM)

    def set_stage(self, index: int) -> None:
        index = max(0, min(index, len(CURRICULUM) - 1))
        with self._lock:
            self._stage = index
            self._queue = []
            self._save_stage()
            self._save_queue()
        self._refill()

    def advance_stage(self) -> bool:
        """Move to next stage. Returns False if already at the last stage."""
        if self._stage >= len(CURRICULUM) - 1:
            return False
        self.set_stage(self._stage + 1)
        return True

    # -------------------------------------------------------------------- #
    # Public interface                                                       #
    # -------------------------------------------------------------------- #

    @property
    def queue(self) -> list[dict]:
        with self._lock:
            return list(self._queue)

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_refill(self) -> str:
        return self._last_refill

    def remove(self, url: str) -> None:
        with self._lock:
            self._queue = [i for i in self._queue if i.get('url') != url]
            self._save_queue()

    def shuffle(self) -> None:
        with self._lock:
            self._queue = []
            self._save_queue()
        self._refill()

    # -------------------------------------------------------------------- #
    # Query generation                                                       #
    # -------------------------------------------------------------------- #

    def _build_queries(self, graph=None) -> list[str]:
        cfg      = STAGE_CONFIG[min(self._stage, len(STAGE_CONFIG) - 1)]
        modifiers = cfg['modifiers']

        stage_topics = list(CURRICULUM[self._stage]['topics'])
        random.shuffle(stage_topics)

        # Mix in a few topics from the previous stage for reinforcement
        if self._stage > 0:
            prev = list(CURRICULUM[self._stage - 1]['topics'])
            random.shuffle(prev)
            stage_topics = stage_topics + prev[:3]

        framed: list[str] = []
        for topic in stage_topics:
            if modifiers:
                mod = random.choice(modifiers)
                framed.append(f"{topic} {mod}")
            else:
                framed.append(topic)

        # Top graph concepts as additional queries (graph feeds itself)
        if graph is not None:
            try:
                top = sorted(
                    graph.all_nodes(),
                    key=lambda n: n['activation_count'],
                    reverse=True,
                )[:10]
                concepts = [n['text'] for n in top]
                random.shuffle(concepts)
                for i in range(0, len(concepts) - 1, 2):
                    pair = f"{concepts[i]} {concepts[i+1]}"
                    if modifiers:
                        pair += f" {random.choice(modifiers)}"
                    framed.insert(0, pair)
            except Exception:
                pass

        return framed

    # -------------------------------------------------------------------- #
    # Refill                                                                 #
    # -------------------------------------------------------------------- #

    def _refill(self, graph=None) -> int:
        with self._lock:
            existing_urls = {i['url'] for i in self._queue}
            needed = MAX_QUEUE - len(self._queue)

        if needed <= 0:
            return 0

        self._status = 'searching…'
        cfg           = STAGE_CONFIG[min(self._stage, len(STAGE_CONFIG) - 1)]
        stage_sources = cfg['sources']
        min_read      = cfg['min_readability']
        gut_topic     = cfg['gutenberg_topic']
        queries       = self._build_queries(graph)
        added         = 0
        new_items: list[dict] = []

        for query in queries:
            if added >= needed:
                break

            n_pick  = min(3, len(stage_sources))
            sources = stage_sources[:n_pick]

            if 'gutenberg' in sources and gut_topic:
                try:
                    gut_results = search_gutenberg(
                        query.split()[0], max_results=4, topic=gut_topic,
                    )
                    for r in gut_results:
                        r['_query'] = query
                        r['_stage'] = self._stage
                        if r.get('url') and r['url'] not in existing_urls:
                            new_items.append(r)
                            existing_urls.add(r['url'])
                            added += 1
                except Exception:
                    pass
                sources = [s for s in sources if s != 'gutenberg']

            try:
                results = search_sources(query, sources, max_per_source=4)
                for r in results:
                    if added >= needed:
                        break
                    if r.get('_error') or not r.get('url'):
                        continue
                    if r['url'] in existing_urls:
                        continue
                    if min_read > 0 and _flesch_score(r.get('snippet', '')) < min_read:
                        continue
                    r['_queued_at'] = datetime.now(tz=timezone.utc).isoformat()
                    r['_query']     = query
                    r['_stage']     = self._stage
                    new_items.append(r)
                    existing_urls.add(r['url'])
                    added += 1
            except Exception:
                pass

        with self._lock:
            self._queue.extend(new_items)
            self._save_queue()

        self._last_refill = datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')
        self._status = 'idle'
        return added

    # -------------------------------------------------------------------- #
    # Background thread                                                      #
    # -------------------------------------------------------------------- #

    def start(self, graph=None) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _worker():
            while True:
                try:
                    with self._lock:
                        size = len(self._queue)
                    if size < MIN_QUEUE:
                        self._refill(graph)
                except Exception:
                    self._status = 'idle'
                time.sleep(CHECK_EVERY)

        self._thread = threading.Thread(target=_worker, daemon=True, name='auto-discover')
        self._thread.start()
