"""
Autonomous discovery — background thread that keeps a persistent queue
of search results topped up to MIN_QUEUE items at all times.

Content follows a developmental curriculum: starts with what a toddler
encounters (colours, animals, simple stories) and advances stage by
stage toward graduate-level abstraction.  The current stage is persisted
in data/discovery_stage.json so progress survives restarts.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime, timezone

from discover import search_sources, SOURCES

_ROOT        = os.path.join(os.path.dirname(__file__), '..')
QUEUE_PATH   = os.path.join(_ROOT, 'data', 'discovery_queue.json')
STAGE_PATH   = os.path.join(_ROOT, 'data', 'discovery_stage.json')
MIN_QUEUE    = 10
MAX_QUEUE    = 20
CHECK_EVERY  = 180   # seconds between refill checks

# --------------------------------------------------------------------------- #
# Developmental curriculum                                                     #
# --------------------------------------------------------------------------- #

CURRICULUM = [
    {
        'label': 'Ages 1–5  (Foundations)',
        'description': 'Concrete, sensory, immediate — the first words a child hears',
        'topics': [
            'animals farm pets',
            'colours shapes sizes',
            'family mother father siblings',
            'food eating fruit vegetables',
            'body parts feelings emotions',
            'weather sun rain wind snow',
            'day night sleep wake',
            'numbers counting simple math',
            'nursery rhymes songs children',
            'fairy tales simple stories',
            'trees flowers plants garden',
            'water river sea beach sand',
            'home house rooms kitchen bedroom',
            'birds insects butterflies nature',
            'toys play imagination pretend',
        ],
    },
    {
        'label': 'Year 1  (Early School)',
        'description': 'First structured learning — letters, community, simple science',
        'topics': [
            'alphabet reading letters words',
            'community helpers doctor teacher firefighter',
            'seasons spring summer autumn winter',
            'habitats animals forest ocean desert',
            'plants growing seeds life cycle',
            'maps directions near far left right',
            'friendship sharing kindness empathy',
            'simple machines wheel lever pulley',
            'healthy food nutrition body',
            'folk tales fables morals',
            'dinosaurs prehistoric animals',
            'solar system sun moon planets',
            'senses sight hearing touch smell',
            'rules community living together',
            'history past present future simple',
        ],
    },
    {
        'label': 'Year 2  (Building Blocks)',
        'description': 'Patterns, systems, first abstractions',
        'topics': [
            'ecosystems food chain predator prey',
            'water cycle rain evaporation clouds',
            'ancient civilisations Egypt Greece Rome',
            'human body organs heart lungs brain',
            'trade goods services money basic economics',
            'mythology legends gods heroes',
            'forces gravity push pull motion',
            'light shadow reflection colour spectrum',
            'habitats adaptation survival',
            'maps continents oceans geography',
            'simple electricity magnets energy',
            'stories characters conflict resolution plot',
            'immigration culture diversity community',
            'time measurement history timeline',
            'rocks minerals earth geology',
        ],
    },
    {
        'label': 'Year 3  (Expanding World)',
        'description': 'Cause and effect, interconnected systems',
        'topics': [
            'democracy government voting laws',
            'industrial revolution history technology',
            'photosynthesis cells biology basics',
            'fractions decimals number systems',
            'climate zones biomes world geography',
            'literature narrative metaphor symbolism',
            'ancient philosophy Socrates Aristotle',
            'chemistry elements atoms molecules',
            'human rights justice equality',
            'economics supply demand trade global',
            'evolution natural selection Darwin',
            'music rhythm harmony composition',
            'art movements impressionism expression',
            'world religions belief systems ethics',
            'environmental issues pollution conservation',
        ],
    },
    {
        'label': 'Year 4  (Systems Thinking)',
        'description': 'Abstract relationships, cause chains, multiple perspectives',
        'topics': [
            'algebra equations variables functions',
            'genetics heredity DNA evolution',
            'world wars history conflict diplomacy',
            'Renaissance science art humanism',
            'waves sound frequency vibration',
            'government systems democracy monarchy',
            'philosophy ethics morality duty',
            'statistics probability data analysis',
            'literature poetry imagery interpretation',
            'ecology biodiversity extinction',
            'economics capitalism markets inequality',
            'sociology culture identity belonging',
            'astronomy cosmology stars galaxies',
            'chemistry reactions bonds periodic table',
            'psychology emotion motivation behaviour',
        ],
    },
    {
        'label': 'Year 5  (Analytical Depth)',
        'description': 'Argument, evidence, multi-causal reasoning',
        'topics': [
            'calculus rates of change limits',
            'molecular biology protein synthesis',
            'political philosophy Locke Rousseau Marx',
            'thermodynamics energy entropy heat',
            'literary theory criticism analysis',
            'macroeconomics GDP inflation fiscal policy',
            'electromagnetism fields waves Maxwell',
            'cognitive psychology memory learning',
            'world history colonialism imperialism',
            'computer science algorithms data structures',
            'linguistics syntax semantics pragmatics',
            'ethics consequentialism deontology virtue',
            'neuroscience brain neural circuits',
            'quantum mechanics wave particle duality',
            'sociology institutions power inequality',
        ],
    },
    {
        'label': 'Year 6  (Specialist Knowledge)',
        'description': 'Discipline-specific depth and technical precision',
        'topics': [
            'machine learning neural networks deep learning',
            'philosophy of mind consciousness qualia',
            'topology manifolds abstract algebra',
            'molecular genetics CRISPR gene expression',
            'econometrics causal inference regression',
            'fluid dynamics turbulence chaos',
            'comparative literature postcolonial theory',
            'number theory prime cryptography',
            'immunology adaptive immune response',
            'political economy globalisation power',
            'formal logic proof theory model theory',
            'astrophysics dark matter relativity',
            'complex systems emergence self-organisation',
            'information theory entropy compression',
            'historiography primary sources interpretation',
        ],
    },
    {
        'label': 'Graduate  (Frontier)',
        'description': 'Open problems, uncertainty, cutting-edge research',
        'topics': [
            'consciousness hard problem qualia binding',
            'artificial general intelligence alignment',
            'quantum gravity string theory holography',
            'origin of life abiogenesis prebiotic chemistry',
            'free will determinism compatibilism',
            'foundations of mathematics Gödel incompleteness',
            'dark energy cosmological constant vacuum',
            'language models semantics grounding meaning',
            'social complexity emergence institutions',
            'epigenetics gene environment interaction',
            'consciousness integrated information theory',
            'causal inference counterfactuals intervention',
            'post-quantum cryptography lattice problems',
            'philosophy of physics time arrow entropy',
            'metacognition self-reference strange loops',
        ],
    },
]

ALL_SOURCES = list(SOURCES.keys())


# --------------------------------------------------------------------------- #
# AutoDiscovery                                                                #
# --------------------------------------------------------------------------- #

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

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Stage control                                                        #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Query generation                                                     #
    # ------------------------------------------------------------------ #

    def _build_queries(self, graph=None) -> list[str]:
        stage_topics = list(CURRICULUM[self._stage]['topics'])
        random.shuffle(stage_topics)

        # Occasionally mix in one level below for reinforcement
        if self._stage > 0:
            prev = list(CURRICULUM[self._stage - 1]['topics'])
            random.shuffle(prev)
            stage_topics = stage_topics + prev[:3]

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
                    stage_topics.insert(0, f"{concepts[i]} {concepts[i+1]}")
            except Exception:
                pass

        return stage_topics

    # ------------------------------------------------------------------ #
    # Refill                                                               #
    # ------------------------------------------------------------------ #

    def _refill(self, graph=None) -> int:
        with self._lock:
            existing_urls = {i['url'] for i in self._queue}
            needed = MAX_QUEUE - len(self._queue)

        if needed <= 0:
            return 0

        self._status = 'searching…'
        queries   = self._build_queries(graph)
        added     = 0
        new_items: list[dict] = []

        for query in queries:
            if added >= needed:
                break
            sources = random.sample(ALL_SOURCES, min(3, len(ALL_SOURCES)))
            try:
                results = search_sources(query, sources, max_per_source=4)
                for r in results:
                    if r.get('_error') or not r.get('url'):
                        continue
                    if r['url'] in existing_urls:
                        continue
                    r['_queued_at'] = datetime.now(tz=timezone.utc).isoformat()
                    r['_query']     = query
                    r['_stage']     = self._stage
                    new_items.append(r)
                    existing_urls.add(r['url'])
                    added += 1
                    if added >= needed:
                        break
            except Exception:
                pass

        with self._lock:
            self._queue.extend(new_items)
            self._save_queue()

        self._last_refill = datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')
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
                    with self._lock:
                        size = len(self._queue)
                    if size < MIN_QUEUE:
                        self._refill(graph)
                except Exception:
                    self._status = 'idle'
                time.sleep(CHECK_EVERY)

        self._thread = threading.Thread(target=_worker, daemon=True, name='auto-discover')
        self._thread.start()
