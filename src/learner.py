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

import json, os, queue, random, re, sys, threading, time
import psutil as _psutil
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
# i5-14400F (10c/16t) · 32GB RAM · RTX 3050 4GB · CPU-only torch
#
# IO-bound threads (HTTP): OS handles select/epoll — 100+ threads is fine.
# CPU-bound threads (trafilatura, numpy): match logical processors (16).
# EmbeddingIndex updates: pure numpy, 8 workers is plenty.
#
CYCLE_TIME        = 0      # 0 = no idle sleep — run cycles back-to-back continuously
N_WORKERS_SEARCH  = 256    # pure IO — scale up for more simultaneous searches
N_WORKERS_IO      = 256    # HTTP download pool — saturate bandwidth
N_WORKERS_PARSE   = 16     # trafilatura (CPU, GIL-releasing) — logical CPUs
N_WORKERS_FETCH   = 256    # alias for IO pool
N_WORKERS_GRAPH   = 8      # EmbeddingIndex + field ingest
N_WORKERS         = 16     # legacy fallback
N_ENCODER_WORKERS = 4      # parallel encoder threads — never bottleneck on embedding
TOPICS_PER_CYCLE  = 200    # 200 topics × 15 results = 3000 article candidates/cycle
MAX_RESULTS_PER   = 15     # results per topic search — wider net
MAX_SENTENCES     = 25     # per-article sentence cap — deeper reading
MIN_SENTENCES     = 2
FETCH_TIMEOUT     = 8      # slightly longer — tolerate slower sites
SEARCH_TIMEOUT    = 5      # more time to collect search results
CHECKIN_EVERY     = 10 * 60
FEED_MAX          = 500    # larger live feed

def _lcfg(key: str, default):
    try:
        from config import Config
        v = Config.get_instance().get('learning', key)
        return type(default)(v) if v is not None else default
    except Exception:
        return default

# ── Paths ──────────────────────────────────────────────────────────────────────
_STATS_PATH      = _DATA / 'learner_stats.json'
_FEED_PATH       = _DATA / 'live_feed.jsonl'
_PAUSED_PATH     = _DATA / 'paused.txt'
_FSTATUS         = _DATA / 'fetch_status.json'
_UMBER_LIVE_PATH = _DATA / 'umber_live.json'

# ── Topics ─────────────────────────────────────────────────────────────────────
TOPICS = [
    # ── Core cognition & mind ─────────────────────────────────────────────
    'consciousness perception cognition', 'philosophy of mind identity self',
    'neuroscience memory learning brain', 'cognitive science mental models',
    'sleep dreams unconscious mind', 'attention awareness metacognition',
    'embodied cognition situated intelligence', 'phenomenology qualia experience',
    'introspection self-reflection inner speech', 'imagination creativity insight',
    'decision making heuristics bias', 'emotion regulation affect',
    'motivation volition agency', 'social cognition theory of mind',
    'language thought Sapir-Whorf hypothesis', 'mental illness psychiatry treatment',
    # ── Physics & mathematics ─────────────────────────────────────────────
    'quantum mechanics wave particle duality', 'thermodynamics entropy energy systems',
    'astrophysics black holes spacetime', 'physics relativity spacetime curvature',
    'optics light photons electromagnetism', 'chaos theory dynamical systems bifurcation',
    'topology knots manifolds geometry', 'mathematics abstract algebra group theory',
    'number theory prime distribution', 'probability statistics inference',
    'fluid dynamics turbulence flow', 'nuclear physics atomic structure',
    'particle physics standard model', 'cosmology dark matter universe expansion',
    'string theory extra dimensions', 'condensed matter superconductivity',
    'complexity theory computational limits', 'information theory entropy coding',
    # ── Biology & life sciences ───────────────────────────────────────────
    'evolution natural selection adaptation', 'genetics DNA protein expression',
    'ecology ecosystems biodiversity', 'evolutionary biology animal behaviour',
    'cell biology organelles metabolism', 'microbiology viruses bacteria',
    'immunology adaptive innate response', 'neurobiology synaptic plasticity',
    'developmental biology embryogenesis', 'epigenetics gene regulation',
    'ecology predator prey dynamics', 'marine biology ocean ecosystems',
    'plant biology photosynthesis', 'astrobiology origin of life',
    'palaeontology fossil record extinction', 'biomechanics locomotion anatomy',
    # ── Chemistry & materials ─────────────────────────────────────────────
    'chemistry molecular bonds reactions', 'biochemistry metabolism pathways',
    'materials science crystalline structure', 'polymer chemistry soft matter',
    'electrochemistry battery energy storage', 'nanotechnology molecular machines',
    'catalysis reaction kinetics', 'spectroscopy light matter interaction',
    # ── Earth & environment ───────────────────────────────────────────────
    'climate change atmospheric physics', 'oceanography fluid dynamics tides',
    'geology plate tectonics volcanism', 'hydrology water cycle precipitation',
    'ecology soil microbiome agriculture', 'environmental chemistry pollution',
    'glaciology ice sheets sea level', 'atmospheric chemistry ozone aerosols',
    'seismology earthquake fault lines', 'geomorphology landscape erosion',
    # ── Human & social sciences ───────────────────────────────────────────
    'anthropology human origins culture', 'sociology culture social structures',
    'social psychology group identity', 'psychology behaviour motivation',
    'economics game theory decision making', 'political theory power governance',
    'ethics morality free will determinism', 'philosophy of language meaning truth',
    'philosophy of science epistemology', 'logic inference formal systems',
    'linguistics phonology grammar pragmatics', 'geopolitics power conflict diplomacy',
    'history civilisation collapse empire', 'ancient history religion ritual belief',
    'demography population migration', 'urban studies city planning',
    'legal theory justice rights', 'cultural studies identity representation',
    # ── Language & literature ─────────────────────────────────────────────
    'literature narrative symbolism metaphor', 'poetry verse language rhythm',
    'mythology symbolism archetype narrative', 'semiotics signs meaning systems',
    'rhetoric persuasion discourse', 'translation language equivalence',
    'oral tradition storytelling folklore', 'modernist postmodernist literature',
    # ── Arts & aesthetics ─────────────────────────────────────────────────
    'art perception aesthetics creativity', 'music harmony rhythm acoustics',
    'architecture space form structure', 'film theory cinematography narrative',
    'visual art colour form composition', 'dance movement embodiment',
    'photography image representation', 'theatre performance ritual',
    # ── Technology & computation ──────────────────────────────────────────
    'emergence complexity self-organisation', 'artificial intelligence reasoning',
    'neural networks deep learning', 'robotics autonomous systems',
    'information theory communication', 'cryptography security privacy',
    'distributed systems consensus', 'computer vision perception',
    'natural language processing semantics', 'reinforcement learning reward',
    'systems biology network dynamics', 'synthetic biology engineering life',
    # ── Philosophy & metaphysics ──────────────────────────────────────────
    'time perception duration memory', 'causality determinism counterfactuals',
    'ontology existence being nothing', 'metaphysics space time matter',
    'philosophy of mathematics platonism', 'ethics consequentialism deontology',
    'existentialism absurdism meaning', 'philosophy of religion mysticism',
    'epistemology knowledge justification', 'mind-body problem dualism',
    # ── Interdisciplinary ─────────────────────────────────────────────────
    'human emotion grief joy love longing', 'epidemiology population health risk',
    'food systems nutrition metabolism', 'poverty inequality development',
    'religion spirituality contemplation', 'meditation mindfulness awareness',
    'memory trauma healing resilience', 'play games strategy competition',
    'death mortality afterlife belief', 'beauty sublime transcendence',
    'pattern recognition symmetry form', 'systems thinking feedback loops',
    'network theory small world hubs', 'collective intelligence swarm',
    'evolutionary psychology mating cooperation', 'cognitive load working memory',
    # ── Animals ───────────────────────────────────────────────────────────────
    'mammals anatomy physiology behaviour', 'birds avian evolution flight',
    'reptiles amphibians cold-blooded vertebrates', 'fish aquatic vertebrates ocean',
    'insects arthropods exoskeleton colony', 'arachnids spiders scorpions venom',
    'marine mammals whales dolphins cetaceans', 'primate evolution ape monkey',
    'dog wolf domestication canine', 'cat feline predator territory',
    'apex predators lion tiger leopard', 'elephant memory social herd',
    'migration seasonal animal movement', 'animal communication vocalisation',
    'camouflage mimicry deception animals', 'bioluminescence deep sea creatures',
    'parasite host relationship symbiosis', 'animal cognition tool use problem solving',
    # ── Plants & fungi ────────────────────────────────────────────────────────
    'flowering plants angiosperm reproduction', 'trees forest canopy root system',
    'fungi mycelium decomposition network', 'moss lichen primitive land plants',
    'ferns spores ancient plant lineage', 'succulents desert plant adaptation',
    'tropical rainforest plant diversity', 'grassland prairie savanna ecosystem',
    'seeds germination growth dispersal', 'pollination flower bee symbiosis',
    'medicinal plants herbal compounds', 'food crops agriculture domestication',
    # ── Materials & substances ────────────────────────────────────────────────
    'metals iron steel copper alloy', 'precious metals gold silver platinum',
    'stone rock mineral geological formation', 'wood timber grain cellulose',
    'glass silica transparency optical', 'ceramics clay firing pottery',
    'rubber latex elasticity polymer', 'plastics synthetic polymer petroleum',
    'textiles fibre weaving fabric', 'leather hide tanning animal skin',
    'sand gravel sediment particle size', 'ice snow frozen water crystal',
    'salt mineral sodium chloride ocean', 'soil earth loam composition',
    'water liquid properties surface tension', 'oil petroleum hydrocarbon energy',
    # ── Tools & instruments ───────────────────────────────────────────────────
    'hand tools hammer saw drill chisel', 'cutting tools blade edge sharpness',
    'measuring instruments ruler gauge sensor', 'musical instruments string wind percussion',
    'optical instruments lens telescope microscope', 'surgical instruments precision medicine',
    'farming tools plough hoe irrigation', 'weapons sword bow projectile',
    'navigation compass map sextant', 'computing hardware processor memory',
    # ── Food & drink ──────────────────────────────────────────────────────────
    'bread grain wheat fermentation baking', 'meat protein cooking preparation',
    'vegetables root leaf stem nutrition', 'fruit sugar fructose ripening',
    'dairy milk cheese fermentation lactose', 'spices herbs flavour compounds',
    'fermented foods vinegar alcohol fermentation', 'oil fat cooking lipid',
    'sugar sweetener carbohydrate energy', 'salt preservative mineral seasoning',
    'fish seafood ocean protein nutrition', 'eggs protein nutrition cooking',
    'tea coffee caffeine stimulant drink', 'wine beer fermentation alcohol culture',
    'chocolate cacao flavonoid confection', 'rice wheat corn staple grain crop',
    # ── Vehicles & transport ──────────────────────────────────────────────────
    'automobile engine combustion vehicle', 'bicycle wheel pedal transport',
    'aircraft wing lift aerodynamics flight', 'ship hull buoyancy ocean vessel',
    'train locomotive rail network', 'rocket propulsion orbit space',
    'submarine underwater pressure vessel', 'motorcycle two-wheel engine speed',
    'electric vehicle battery motor range', 'boat sail wind water vessel',
    # ── Buildings & structures ────────────────────────────────────────────────
    'house home dwelling shelter design', 'bridge engineering span load',
    'skyscraper high-rise steel glass structure', 'temple cathedral religious architecture',
    'castle fortress medieval defence', 'dam reservoir water energy structure',
    'tunnel underground excavation engineering', 'tower antenna communication height',
    'stadium arena crowd sport design', 'factory industrial production building',
    # ── Human body & anatomy ──────────────────────────────────────────────────
    'brain nervous system neural anatomy', 'heart circulatory blood vessel',
    'lung respiration oxygen exchange', 'liver kidney organ detoxification',
    'bone skeleton joint cartilage', 'muscle fibre contraction movement',
    'skin dermis epidermis barrier', 'eye retina vision light reception',
    'ear cochlea hearing sound vibration', 'immune system lymph white blood cell',
    'hormones endocrine gland regulation', 'digestion stomach intestine enzyme',
    # ── Weather & natural phenomena ───────────────────────────────────────────
    'lightning thunder storm electricity', 'wind pressure atmosphere movement',
    'rain precipitation cloud water cycle', 'volcano eruption lava magma',
    'earthquake fault seismic wave ground', 'flood river overflow erosion',
    'rainbow light refraction spectrum', 'aurora polar lights magnetic field',
    'tornado cyclone storm vortex pressure', 'snow ice crystal temperature',
    # ── Places & geography ────────────────────────────────────────────────────
    'mountain peak altitude glacier', 'desert arid sand dune climate',
    'ocean depth trench salinity current', 'river delta estuary sediment',
    'forest canopy biodiversity ecosystem', 'island isolation endemic species',
    'cave underground formation stalactite', 'valley canyon erosion landscape',
    'reef coral marine ecosystem biodiversity', 'tundra permafrost arctic ecosystem',
]

# ── Code-content filter ────────────────────────────────────────────────────────
_CODE_KEYWORDS = {
    # Programming / software
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
    # AI assistant / prompt noise
    'suno ai', 'stable diffusion', 'midjourney', 'dall-e', 'chatgpt', 'gpt-4',
    'llm prompt', 'system prompt', 'prompt engineering', 'ai assistant',
    'generating images', 'image generation prompt', 'style prompt', 'meta tag',
    # Commerce / SEO / ads
    'buy now', 'add to cart', 'sign up for', 'subscribe to', 'click here',
    'terms of service', 'privacy policy', 'cookie policy', 'copyright',
    'all rights reserved', 'sponsored', 'advertisement',
    # Reddit / social noise
    'upvote', 'downvote', 'subreddit', 'posted by u/', 'crossposted',
    # Review / cinematic noise
    'cinematography', 'box office', 'movie review', 'film review',
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
        self._index         = None   # EmbeddingIndex (replaces SemanticGraph)
        self._field         = None
        self._dynamics      = None
        self._umber         = None
        self._umber_live_ts = 0.0       # throttle for _write_umber_live()
        self._known_urls: set = set()          # O(1) lookup
        self._known_urls_q: deque = deque(maxlen=50000) # bounded eviction queue
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
        # Persistent pools — created once, reused every cycle to avoid thread-create overhead
        self._search_pool = ThreadPoolExecutor(max_workers=N_WORKERS_SEARCH, thread_name_prefix='search')
        self._io_pool     = ThreadPoolExecutor(max_workers=N_WORKERS_IO,     thread_name_prefix='fetch_io')
        self._cpu_pool    = ThreadPoolExecutor(max_workers=N_WORKERS_PARSE,  thread_name_prefix='fetch_cpu')
        self._graph_pool  = ThreadPoolExecutor(max_workers=N_WORKERS_GRAPH,  thread_name_prefix='graph')
        # Async encoder queue — encode + ingest runs in background, never blocks cycle
        # queue.Queue: each of the 16 encoder workers blocks on get() independently —
        # no thundering-herd on a shared event; maxsize=64 caps memory at high speed
        self._encode_q: queue.Queue = queue.Queue(maxsize=64)

    def _ensure_index(self) -> None:
        if self._index is None:
            try:
                from embed_index import EmbeddingIndex
                self._index = EmbeddingIndex.get()
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

    def _ensure_umber(self) -> None:
        if self._umber is not None:
            return
        try:
            import sys as _sys
            _jam = str(_ROOT / 'jam')
            if _jam not in _sys.path:
                _sys.path.insert(0, _jam)
            from umber import Interpreter
            _u = Interpreter()
            _u.load(str(_ROOT / 'jam' / 'cognition.umber'))
            self._umber = _u
            log.info('UMBER cognition field loaded')
        except Exception as e:
            log.debug('umber init error: %s', e)

    def _write_umber_live(self) -> None:
        """Write full UMBER field state in UMBRA WebSocket format to umber_live.json."""
        if self._umber is None:
            return
        try:
            f = self._umber.field
            top = sorted(f.activations.items(), key=lambda x: -x[1])[:80]
            snap = {
                'tick':             f.tick,
                'entropy':          round(f.entropy(), 4),
                'total_activation': round(f.total_activation(), 4),
                'mode':             self._umber.current_mode(),
                'dominant_goal':    self._umber.dominant_goal(),
                'concepts': [
                    {'id': k, 'activation': round(v, 4),
                     **{pk: float(pv) for pk, pv in f.concepts.get(k, {}).items()}}
                    for k, v in top if v > 0.001
                ],
                'contradictions': [
                    {'id': k, 'a': c['a'], 'b': c['b'], 'tension': round(c['tension'], 4)}
                    for k, c in f.contradictions.items()
                ],
                'goals': [
                    {'id': k, 'weight': round(w, 4)}
                    for k, w in f.goals.items()
                ],
                'identity': {k: round(v, 4) for k, v in f.identity.items()},
                'beliefs': [
                    {
                        'id': bname, 'claim_type': b.claim_type,
                        'a': b.a, 'b': b.b,
                        'confidence': round(b.confidence, 4),
                        'evidence_count': b.evidence_count,
                        'tombstone': b.tombstone > 0,
                    }
                    for bname, b in sorted(f.beliefs.items(), key=lambda x: -x[1].confidence)
                    if b.tombstone == 0
                ],
                'collapse': False,
                'collapse_reason': '',
            }
            tmp = _UMBER_LIVE_PATH.with_suffix('.tmp')
            tmp.write_text(json.dumps(snap), encoding='utf-8')
            tmp.replace(_UMBER_LIVE_PATH)
        except Exception as e:
            log.debug('umber_live write error: %s', e)

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
        # Append-only: never rewrite the whole buffer, just add a line.
        # The server reads the tail via seek(), so full rewrites are wasteful.
        try:
            line = json.dumps(entry, ensure_ascii=False)
            with self._feed_lock:
                self._feed_buf.append(line)
                with open(_FEED_PATH, 'a', encoding='utf-8') as fh:
                    fh.write(line + '\n')
        except Exception:
            pass

    def _feed_flush(self) -> None:
        pass  # no-op — append-only now, nothing to flush

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
            if len(self._subsys_buf) > 20000:
                self._subsys_buf = self._subsys_buf[-20000:]

    # ── Background workers ────────────────────────────────────────────────────

    def _regulator_worker(self) -> None:
        """Regulation tick every 60s — Layer 4. Also ticks GoalGraph."""
        time.sleep(60)
        while True:
            try:
                if self._field is not None:
                    from regulation import Regulation
                    reg = Regulation.get()
                    reg.tick(self._field)
                    # GoalGraph tick — driven by same field snapshot
                    try:
                        from goal_graph import GoalGraph
                        snap = self._field.snapshot()
                        GoalGraph.get().tick(snap, reg.mode, reg.goal)
                    except Exception as ge:
                        log.debug('goal_graph tick error: %s', ge)
            except Exception as e:
                log.debug('regulation error: %s', e)
            time.sleep(60)

    def _umber_worker(self) -> None:
        """UMBER field tick every 60s — source of truth for mode/goal."""
        time.sleep(70)
        while True:
            try:
                if self._umber is not None:
                    self._umber.tick()
                    mode = self._umber.current_mode()
                    goal = self._umber.dominant_goal()

                    # cog_status.json — read by overlay, server, worldview
                    _cog = _DATA / 'cog_status.json'
                    tmp = _cog.with_suffix('.tmp')
                    tmp.write_text(
                        json.dumps({'mode': mode, 'goal': goal}),
                        encoding='utf-8'
                    )
                    tmp.replace(_cog)

                    # patch mode/goal into regulation.json so server sees consistent state
                    _reg = _DATA / 'regulation.json'
                    try:
                        reg = json.loads(_reg.read_text(encoding='utf-8')) if _reg.exists() else {}
                        reg['mode'] = mode
                        reg['goal'] = goal
                        tmp2 = _reg.with_suffix('.tmp')
                        tmp2.write_text(json.dumps(reg, indent=2), encoding='utf-8')
                        tmp2.replace(_reg)
                    except Exception:
                        pass

                    # feed goal-mode log for worldview surviving_goals
                    self._umber._goal_mode_log.append({
                        'goal': goal, 'mode': mode, 'ts': time.time()
                    })
                    if len(self._umber._goal_mode_log) > 2000:
                        self._umber._goal_mode_log = self._umber._goal_mode_log[-2000:]

                    log.debug('umber tick: mode=%s goal=%s', mode, goal)

                    # ── Write umber_state.json for server/UI ──────────────
                    try:
                        f = self._umber.field
                        beliefs_out = []
                        for bname, b in sorted(
                            f.beliefs.items(), key=lambda x: -x[1].confidence
                        )[:40]:
                            if b.tombstone == 0:
                                beliefs_out.append({
                                    'name':       bname,
                                    'claim_type': b.claim_type,
                                    'a':          b.a,
                                    'b':          b.b,
                                    'confidence': round(b.confidence, 4),
                                    'age':        b.age,
                                    'evidence':   b.evidence_count,
                                })
                        contras_out = []
                        for cname, c in sorted(
                            f.contradictions.items(), key=lambda x: -x[1]['tension']
                        )[:20]:
                            contras_out.append({
                                'name':    cname,
                                'a':       c['a'],
                                'b':       c['b'],
                                'tension': round(c['tension'], 4),
                            })
                        snap = {
                            'mode':          mode,
                            'goal':          goal,
                            'concepts':      len(f.concepts),
                            'beliefs':       beliefs_out,
                            'contradictions': contras_out,
                            'goals': {k: round(v, 4) for k, v in f.goals.items()},
                            'identity': {k: round(v, 4) for k, v in f.identity.items()},
                            'tick':          f.tick,
                            'updated_at':    datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
                        }
                        _us = _DATA / 'umber_state.json'
                        tmp3 = _us.with_suffix('.tmp')
                        tmp3.write_text(json.dumps(snap, indent=2), encoding='utf-8')
                        tmp3.replace(_us)
                    except Exception:
                        pass

            except Exception as e:
                log.debug('umber tick error: %s', e)
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

            # ── Contradiction detection → UMBER field ──────────────────────
            try:
                from contradiction import ContradictionRegistry
                reg = ContradictionRegistry.get()
                n_open_before = len(reg.unresolved())
                new_contras = reg.observe(texts)
                n_open_after = len(reg.unresolved())
                if self._umber is not None and new_contras:
                    for c in new_contras:
                        a, b = c['concept_a'], c['concept_b']
                        tension = (c.get('tension_a', 0.3) + c.get('tension_b', 0.3)) / 2
                        slug = f"_contra_{a[:20]}_{b[:20]}".replace(' ', '_')
                        self._umber.inject_contradiction(slug, a, b, tension)
                # Energy reward for net contradiction reduction (stabilization)
                n_resolved = max(0, n_open_before - n_open_after)
                if n_resolved > 0:
                    try:
                        _ep = _DATA / 'energy.json'
                        _ed = json.loads(_ep.read_text(encoding='utf-8')) if _ep.exists() else {}
                        _ecur = float(_ed.get('current', 1.0))
                        _reward = min(0.05 * n_resolved, 0.15)
                        _ed['current'] = round(min(1.0, _ecur + _reward), 6)
                        _ed['ts'] = time.time()
                        _tmp = _ep.with_suffix('.tmp')
                        _tmp.write_text(json.dumps(_ed), encoding='utf-8')
                        _tmp.replace(_ep)
                    except Exception:
                        pass
            except Exception as e:
                log.debug('contradiction→umber error: %s', e)

            # ── World model inference → UMBER beliefs ──────────────────────
            try:
                from world_model import WorldModel
                wm = WorldModel.get()
                wm.infer_from_context(texts)
                if self._umber is not None:
                    for edge in wm.top_edges(20):
                        s, t, rel = edge['source'], edge['target'], edge['relation']
                        conf = edge['confidence']
                        # map world_model relations to UMBER claim types
                        claim = 'predicts' if rel in ('predicts', 'depends_on') else 'causes'
                        slug = f"_wm_{s[:15]}_{claim}_{t[:15]}".replace(' ', '_')
                        self._umber.inject_belief(slug, claim, s, t, conf)
            except Exception as e:
                log.debug('world_model→umber error: %s', e)

            try:
                from episodes  import EpisodeStore
                from predictor import Predictor
                EpisodeStore.get().record(texts, ambiguity=0.0, region=None)
                Predictor.get().pre_activate(texts, None)
            except Exception:
                pass

    # ── Background cache pre-warmer ───────────────────────────────────────────

    def _prefetch_worker(self) -> None:
        """
        Pre-warm search + URL caches so the cycle's search and fetch phases
        are pure in-memory lookups (~0.01s) instead of HTTP round-trips (3-6s).

        Strategy:
          Phase A — call every source search function for every topic in parallel.
                    search_wikipedia(), search_reddit(), etc. store results in
                    _search_cache (30-min TTL). The cycle's _search() call then
                    hits the cache instead of the network.
          Phase B — call fetch_raw() for every article URL returned by Phase A.
                    fetch_raw() stores article text in _url_cache (1-hour TTL).
                    The cycle's fetch_raw() call then hits the cache.

        Runs immediately at startup (first iteration: no sleep), then every
        24 minutes — just inside the 30-minute search cache TTL.
        """
        from sources import SOURCES, fetch_raw, parse_raw

        # Pre-warm all active sources — search + URL + sentence caches all populated here
        _WARM_SRC = [s for s in (
            'wikipedia', 'simplewiki', 'arxiv', 'openalex', 'semanticscholar',
            'pubmed', 'crossref', 'reddit', 'hackernews', 'stackexchange',
            'devto', 'gutenberg', 'newsrss', 'internetarchive', 'web',
            'bing_web', 'ddg_news', 'wikidata', 'conceptnet',)
                     if s in SOURCES]

        first_run = True
        while True:
            if not first_run:
                time.sleep(24 * 60)   # refresh before 30-min search cache TTL
            first_run = False

            try:
                t0 = time.perf_counter()
                all_items: list[dict] = []

                # Phase A: warm search cache — parallel across all topics × sources
                def _warm_search(topic: str, src: str) -> list[dict]:
                    try:
                        results = SOURCES[src][0](topic, max_results=20)
                        for r in results:
                            r['topic']  = topic
                            r['source'] = src
                        return results
                    except Exception:
                        return []

                sfuts = {
                    self._search_pool.submit(_warm_search, topic, src): (topic, src)
                    for topic in TOPICS
                    for src in _WARM_SRC
                }
                for fut in as_completed(sfuts, timeout=25):
                    try:
                        all_items.extend(fut.result())
                    except Exception:
                        pass

                # Phase B: warm URL cache + sentence cache — IO then CPU parse
                text_items = [
                    i for i in all_items
                    if i.get('url') and i.get('source') not in ('image', 'audio')
                ]
                # Stage 1: HTTP download in parallel (warm _url_cache)
                raw_map: dict = {}
                ffuts = {self._io_pool.submit(fetch_raw, item): item for item in text_items}
                for fut in as_completed(ffuts, timeout=90):
                    try:
                        item = ffuts[fut]
                        raw_map[item.get('url', '')] = (item, fut.result())
                    except Exception:
                        pass
                # Stage 2: parse in CPU pool (warm _sents_cache via parse_raw)
                pfuts = [
                    self._cpu_pool.submit(parse_raw, item, raw)
                    for url, (item, raw) in raw_map.items()
                ]
                for fut in as_completed(pfuts, timeout=60):
                    try:
                        fut.result()
                    except Exception:
                        pass

                elapsed = time.perf_counter() - t0
                log.info('prefetch: %d articles warmed in %.1fs — cycle fetch now ~0s',
                         len(text_items), elapsed)

            except Exception as e:
                log.debug('prefetch worker error: %s', e)

    # ── Background encoder worker ─────────────────────────────────────────────

    def _encoder_worker(self) -> None:
        """
        Async encode + ingest — runs in a dedicated daemon thread.
        The main cycle pushes (all_sents, fetched, boundaries) to _encode_q
        and returns immediately (t_extract ≈ 0.00s). This worker encodes the
        batch, updates the EmbeddingIndex, ingests into the JAM field, and
        emits feed entries — one batch at a time.
        """
        while True:
            try:
                payload = self._encode_q.get(timeout=5)
            except queue.Empty:
                continue
            try:
                self._encode_and_ingest(payload)
            except Exception as e:
                log.warning('encoder worker error: %s', e)

    def _encode_and_ingest(self, payload: tuple) -> None:
        _t0 = time.perf_counter()
        all_sents, fetched, boundaries, mm_items_enc = payload

        # Pre-warm LRU from index for sentences we've seen before
        from extractor import extract_batch, _embed_cache, _cache_put  # noqa: PLC0415
        if self._index is not None and all_sents:
            try:
                with self._index._lock:
                    _idx  = self._index._text_idx
                    _embs = self._index._embs
                    for s in all_sents:
                        if s not in _embed_cache and s in _idx:
                            _cache_put(s, _embs[_idx[s]].tolist())
            except Exception:
                pass

        _t_extract_start = time.perf_counter()
        mega_batch: list = extract_batch(all_sents) if all_sents else []
        _t_extract = time.perf_counter() - _t_extract_start

        all_cycle_concepts: list = []
        all_cycle_node_ids: list = []
        ok_entries: list[dict]   = []
        n_concepts_total = n_sents_total = 0

        for item, (s, e) in zip(fetched, boundaries):
            concepts = [c for batch in mega_batch[s:e] for c in batch]
            if not concepts:
                continue
            node_ids = self._index.update(concepts) if self._index else [''] * len(concepts)
            n_concepts_total += len(concepts)
            n_sents_total    += (e - s)
            all_cycle_concepts.extend(concepts)
            all_cycle_node_ids.extend(node_ids)
            ok_entries.append({
                'ts':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
                'topic':    item.get('topic', ''),
                'title':    item.get('title', '')[:80],
                'source':   item.get('source', ''),
                'url':      item.get('url', ''),
                'concepts': len(concepts),
                'sentences': e - s,
                'status':   'ok',
            })
            # Propagate semantic metadata from wikidata/conceptnet/wordnet into index
            if '_meta' in item and self._index:
                try:
                    self._index.set_meta(item.get('title', ''), **item['_meta'])
                except Exception:
                    pass

        # Multimodal items that were deferred
        for item in mm_items_enc:
            try:
                entry = self._process_multimodal(item)
                if entry['status'] == 'ok':
                    ok_entries.append(entry)
                    all_cycle_concepts.extend([])  # MM has no text concepts
            except Exception:
                pass

        if all_cycle_concepts:
            # Energy economics — spend for novel concepts, earn for consolidation
            try:
                _ep = _DATA / 'energy.json'
                _ed = json.loads(_ep.read_text(encoding='utf-8')) if _ep.exists() else {}
                _ecur = float(_ed.get('current', 1.0))
                _cost_cfg = json.loads((_DATA / 'engine_config.json').read_text(
                    encoding='utf-8')).get('energy', {}) if (_DATA / 'engine_config.json').exists() else {}
                _cost_exp  = float(_cost_cfg.get('cost_exploration',  0.06))
                _replenish = float(_cost_cfg.get('replenish_per_tick', 0.08))
                _n_novel   = sum(1 for c in all_cycle_concepts if getattr(c, 'text', '') not in
                                 (self._field._nodes if self._field else {}))
                _spend = min(_cost_exp * (_n_novel / max(len(all_cycle_concepts), 1)), 0.15)
                _ecur  = max(0.0, min(1.0, _ecur - _spend + _replenish))
                _ed['current'] = round(_ecur, 6)
                _ed['spend_count'] = int(_ed.get('spend_count', 0)) + 1
                _ed['ts'] = time.time()
                _tmp = _ep.with_suffix('.tmp')
                _tmp.write_text(json.dumps(_ed), encoding='utf-8')
                _tmp.replace(_ep)
            except Exception:
                pass

            if self._field is not None:
                try:
                    self._field.ingest(all_cycle_concepts, all_cycle_node_ids)
                except Exception as e:
                    log.debug('field ingest error: %s', e)

            if self._umber is not None:
                try:
                    _goal  = self._umber.dominant_goal()
                    _gain  = {'resolve_tension': 0.04, 'consolidate': 0.02,
                              'stabilise': 0.01, 'expand_knowledge': 0.02,
                              'explore': 0.02}.get(_goal, 0.02)
                    delta  = _gain / max(len(all_cycle_concepts), 1)
                    for c in all_cycle_concepts[:100]:
                        self._umber.inject(c.text, delta)
                except Exception:
                    pass

            _t_detect_start = time.perf_counter()
            _ambiguity_result = None
            try:
                from detector import detect_and_log
                _ambiguity_result = detect_and_log('cycle', all_cycle_concepts[:30])
            except Exception:
                pass
            _t_detect = time.perf_counter() - _t_detect_start

            # IntentCompiler — compile current cognitive intent from field state
            try:
                from intent_compiler import IntentCompiler
                concept_texts = [c.text for c in all_cycle_concepts[:20]]
                amb_dict = None
                if _ambiguity_result is not None:
                    amb_dict = {
                        'score': getattr(_ambiguity_result, 'score', 0.3),
                        'level': getattr(_ambiguity_result, 'level', 'medium'),
                    }
                IntentCompiler.get().compile(concept_texts, amb_dict)
            except Exception as _ie:
                log.debug('intent_compiler error: %s', _ie)

            _t_subsys_start = time.perf_counter()
            self._subsys_queue([c.text for c in all_cycle_concepts])
            _t_subsys = time.perf_counter() - _t_subsys_start

        for entry in ok_entries:
            self._feed_append(entry)
            log.info('learned: %s [%s] +%d concepts',
                     entry['title'], entry['source'], entry['concepts'])

        _t_save_start = time.perf_counter()
        if self._index and n_concepts_total > 0:
            self._index.save()
        _t_save = time.perf_counter() - _t_save_start

        self._stats['total_sentences'] = self._stats.get('total_sentences', 0) + n_sents_total
        self._stats['total_concepts']  = self._stats.get('total_concepts',  0) + n_concepts_total

        # Backfill the most recent perf sample with actual async timings
        _t_total = time.perf_counter() - _t0
        _t_detect_v  = locals().get('_t_detect', 0.0)
        _t_subsys_v  = locals().get('_t_subsys', 0.0)
        with self._perf_lock:
            if self._perf_samples:
                s = self._perf_samples[-1]
                s['extract'] = round(_t_extract, 3)
                s['detect']  = round(_t_detect_v, 3)
                s['subsys']  = round(_t_subsys_v, 3)
                s['save']    = round(_t_save, 3)
                s['total']   = round(s.get('fetch', 0.0) + _t_total, 3)
                s['concepts'] = n_concepts_total

    # ── Multimodal item (image / audio) ───────────────────────────────────────

    def _process_multimodal(self, item: dict) -> dict:
        """
        Image/audio → visual fingerprint → k-NN retrieval → JAM activation.

        1. Embed the image/audio (16×16 pixel fingerprint → 384-dim)
        2. Store the media node itself in the embedding index
        3. Find the 12 nearest text concepts already in the index
        4. Create synthetic Concept objects from those neighbors
        5. Ingest neighbors into JAMField — image/audio "triggers" those concepts
        """
        import numpy as np
        from extractor import Concept

        src = item.get('source', '')
        entry = {
            'ts':       datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            'topic':    item.get('topic', ''),
            'title':    item.get('title', '')[:80],
            'source':   src,
            'url':      item.get('url', ''),
            'concepts': 0,
            'sentences': 0,
            'status':   'ok',
        }

        url   = item.get('url', '')
        label = item.get('title', url)[:200]

        if not url:
            entry['status'] = 'skip:no-url'
            return entry

        vec_arr: np.ndarray | None = None

        if src == 'audio':
            # Audio: embed the title/filename text via MiniLM — already in semantic space.
            # This lets "Thunder.ogg" activate 'thunder', 'storm', 'rain' etc.
            # No transcription, no mel spectrogram — title IS the semantic identity.
            try:
                from extractor import extract
                audio_concepts = extract(label)
                if audio_concepts:
                    vec_arr = np.array(audio_concepts[0].embedding, dtype=np.float32)
            except Exception:
                pass
        else:
            # Image: visual fingerprint via pixel-space projection
            try:
                from multimodal import embed_image
                vec = embed_image(url)
                if vec is not None:
                    vec_arr = np.array(vec, dtype=np.float32)
            except Exception:
                pass

        if vec_arr is None:
            entry['status'] = 'skip:mm-embed-failed'
            return entry

        # Store the media node itself in the embedding index
        media_concept = Concept(text=label, embedding=vec_arr.tolist(), source=src)
        if self._index:
            self._index.update([media_concept])

        # Find 12 nearest text concepts and activate them in the JAM field
        activated = 0
        if self._index and self._index.node_count > 12:
            neighbors = self._index.knn(vec_arr, k=12, exclude={label})
            if neighbors and self._field is not None:
                neighbor_concepts = []
                neighbor_ids = []
                for n_text, _sim in neighbors:
                    n_emb = self._index.get_embedding(n_text)
                    if n_emb is not None:
                        neighbor_concepts.append(
                            Concept(text=n_text, embedding=n_emb.tolist(), source='mm_knn')
                        )
                        neighbor_ids.append('')
                if neighbor_concepts:
                    try:
                        self._field.ingest(neighbor_concepts, neighbor_ids)
                        activated = len(neighbor_concepts)
                    except Exception as e:
                        log.debug('mm field ingest error: %s', e)

        self._stats['total_concepts'] = self._stats.get('total_concepts', 0) + activated
        entry['concepts'] = activated

        # Write to dedicated mm_log.jsonl for visibility
        try:
            with open(_DATA / 'mm_log.jsonl', 'a', encoding='utf-8') as _mf:
                import json as _json
                _mf.write(_json.dumps({
                    'ts':        entry['ts'],
                    'source':    src,
                    'title':     label[:100],
                    'url':       url[:200],
                    'activated': activated,
                    'status':    entry['status'],
                }) + '\n')
        except Exception:
            pass

        return entry

    # ── One full cycle ────────────────────────────────────────────────────────

    def _write_perf_profile(self, cycle: dict) -> None:
        with self._perf_lock:
            samples = list(self._perf_samples)
        if not samples:
            return
        keys = ('fetch', 'extract', 'detect', 'graph', 'save', 'subsys', 'total')
        n = len(samples)
        # One-pass accumulation instead of O(k×n) nested comprehension
        sums: dict[str, float] = {k: 0.0 for k in keys}
        for s in samples:
            for k in keys:
                sums[k] += s.get(k, 0.0)
        avg = {k: round(sums[k] / n, 3) for k in keys}
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

    # ── Resource guard (RAM only — CPU is expected to run high) ──────────────
    _RAM_LIMIT   = 95.0        # max % RAM before backing off
    _RES_BACKOFF = 5.0         # seconds to sleep when RAM is critical

    def _check_resources(self) -> None:
        """Block only if RAM is critically full — never throttle on CPU alone."""
        limit = float(_lcfg('ram_limit_pct', self._RAM_LIMIT))
        ram = _psutil.virtual_memory().percent
        if ram < limit:
            return
        while ram >= limit:
            log.debug('RAM guard: %.1f%% used — backing off %.1fs', ram, self._RES_BACKOFF)
            time.sleep(self._RES_BACKOFF)
            ram = _psutil.virtual_memory().percent

    def _cycle(self) -> None:
        self._check_resources()
        self._ensure_index()
        self._ensure_field()
        self._ensure_umber()

        nws  = _lcfg('n_workers_search',  N_WORKERS_SEARCH)
        nwf  = _lcfg('n_workers_fetch',   N_WORKERS_IO)      # IO download pool
        nwp  = _lcfg('n_workers_parse',   N_WORKERS_PARSE)   # CPU parse pool
        nwg  = _lcfg('n_workers_graph',   N_WORKERS_GRAPH)
        tpc  = _lcfg('topics_per_cycle',  TOPICS_PER_CYCLE)
        ft   = _lcfg('fetch_timeout',     FETCH_TIMEOUT)
        st   = _lcfg('search_timeout',    SEARCH_TIMEOUT)
        mxs  = _lcfg('max_sentences_per_article', MAX_SENTENCES)
        mins = _lcfg('min_sentences',     MIN_SENTENCES)
        mxr  = _lcfg('max_results_per',   MAX_RESULTS_PER)

        all_sources = list(SOURCES.keys())

        # ── Energy economics — gate cycle depth by available energy ───────────
        _energy_path = _DATA / 'energy.json'
        _energy_level = 1.0
        try:
            _edata = json.loads(_energy_path.read_text(encoding='utf-8'))
            _energy_level = float(_edata.get('current', 1.0))
        except Exception:
            pass
        # Scale topics per cycle by energy: 50% energy → 75% topics
        _energy_tpc_scale = 0.5 + 0.5 * _energy_level
        tpc = max(4, int(tpc * _energy_tpc_scale))

        # ── Goal-oriented topic + source selection ─────────────────────────────
        goal_topics: list[str] = []
        current_goal = 'explore'
        if self._umber is not None:
            try:
                current_goal = self._umber.dominant_goal()
                goal_topics  = self._umber.goal_topics(n=max(tpc // 3, 6))
            except Exception:
                pass

        # JAM curiosity topics — high tension+novelty nodes as search queries
        # Filter: only short semantic concepts (not article titles / URLs)
        def _is_concept(text: str) -> bool:
            if len(text) > 60:          return False
            if '::' in text:            return False
            if text.startswith('http'): return False
            if '|' in text:             return False
            if text.count(' ') > 6:     return False
            return True

        jam_curiosity: list[str] = []
        if self._field is not None:
            try:
                _candidates = self._field.top(120, by='tension')
                _scored = sorted(
                    [(t, p) for t, p in _candidates if _is_concept(t)],
                    key=lambda x: x[1].get('tension', 0) * 0.45
                                + x[1].get('novelty', 0) * 0.35
                                + x[1].get('drift', 0)   * 0.20,
                    reverse=True
                )
                jam_curiosity = [t for t, _ in _scored[:max(tpc // 4, 6)]]
            except Exception:
                pass

        # Contradiction-driven topics — concepts in unresolved contradictions
        contra_topics: list[str] = []
        try:
            from contradiction import ContradictionRegistry
            _open = ContradictionRegistry.get().unresolved()[:6]
            for c in _open:
                contra_topics.append(c['concept_a'])
                contra_topics.append(c['concept_b'])
            contra_topics = list(dict.fromkeys(contra_topics))[:6]
        except Exception:
            pass

        # Gap topics — filter out URL/image-title noise before sampling
        gap_topics  = [t for t in _lcfg('gap_topics', []) if _is_concept(t)]
        n_gap       = min(len(gap_topics), max(tpc // 8, 3))
        gap_sample  = random.sample(gap_topics, n_gap) if len(gap_topics) > n_gap else gap_topics

        # Gravitational topics — high-mass concepts anchor exploration
        gravity_topics: list[str] = []
        if self._field is not None:
            try:
                gravity_topics = [t for t, _ in self._field.top(20, by='mass')
                                  if _is_concept(t)][:4]
            except Exception:
                pass

        # Static pool only fills remaining slots — no minimum floor
        _n_dynamic = len(jam_curiosity) + len(contra_topics) + len(gravity_topics) \
                     + len(goal_topics) + len(gap_sample)
        n_static = max(tpc - _n_dynamic, 0)
        static_pool = random.sample(TOPICS, min(n_static, len(TOPICS))) if n_static > 0 else []
        topics = list(dict.fromkeys(
            jam_curiosity + contra_topics + gravity_topics + goal_topics + gap_sample + static_pool
        ))[:tpc]
        log.info('topics | jam=%d contra=%d gravity=%d goal=%d gap=%d static=%d total=%d energy=%.2f',
                 len(jam_curiosity), len(contra_topics), len(gravity_topics),
                 len(goal_topics), len(gap_sample), len(static_pool), len(topics), _energy_level)

        # Source speed profile (i5-14400F measurements):
        # All sources enabled — search results are now cached by prefetch so
        # cycle hits are ~0s. Web (DuckDuckGo) covers the open internet.
        # Arxiv covers academic preprints beyond OpenAlex.
        _FAST_W = {
            # Original sources — rebalanced (wiki was 7x, now equal spread)
            'wikipedia':       2.5,
            'simplewiki':      1.0,
            'arxiv':           2.0,
            'openalex':        2.0,
            'semanticscholar': 2.0,
            'pubmed':          1.8,
            'crossref':        1.5,
            'reddit':          1.8,
            'hackernews':      1.8,
            'stackexchange':   1.5,
            'devto':           1.2,
            'gutenberg':       1.0,
            'newsrss':         1.5,
            'internetarchive': 0.8,
            'web':             2.5,   # DuckDuckGo text search
            'bing_web':        2.0,   # Bing text search — different results set
            'ddg_news':        1.8,   # DuckDuckGo news — current events
            'crawl':           2.2,   # organic link discovery — snowballs from every page
            'wikidata':        2.0,   # structured entity knowledge (type + relations)
            'conceptnet':      2.0,   # commonsense relations (IsA, UsedFor, PartOf...)
            'wordnet':         1.5,   # lexical hierarchy (hypernyms → entity types)
            'image':           3.5,   # visual learning — second priority
            'audio':           5.0,   # audio learning — highest priority
        }
        # Allow UI override of source weights via engine_config.json
        try:
            _cfg_w = _lcfg('source_weights', {})
            if _cfg_w:
                _FAST_W.update({k: float(v) for k, v in _cfg_w.items()})
        except Exception:
            pass

        if self._umber is not None:
            try:
                _src_w = self._umber.goal_source_weights(all_sources)
                _src_w = [w * _FAST_W.get(s, 1.0) for s, w in zip(all_sources, _src_w)]
            except Exception:
                _src_w = [_FAST_W.get(s, 1.0) for s in all_sources]
        else:
            _src_w = [_FAST_W.get(s, 1.0) for s in all_sources]

        # ── Phase 1: Search — fully flattened parallel IO ─────────────────────
        # Every (topic, source) pair is its own future — no sequential grouping.
        # 200 topics × 5 sources = 1000 concurrent tasks, wall time = slowest one.
        _active_sources = [s for s, w in zip(all_sources, _src_w) if w > 0]
        _active_weights = [w for w in _src_w if w > 0]

        def _one_search(topic: str, src: str) -> list[dict]:
            try:
                rs = SOURCES[src][0](topic, max_results=mxr)
                for r in rs:
                    r['topic'] = topic; r['source'] = src
                return rs
            except Exception:
                return []

        # Build (topic, src) task list — always include audio+image if active,
        # then fill remaining slots from weighted text sources.
        # Priority order: audio (50%) → image (34%) → text (16%)
        _mm_guaranteed = [s for s in ('audio', 'image') if s in _active_sources]
        _text_sources  = [s for s in _active_sources if s not in ('audio', 'image')]
        _text_weights  = [w for s, w in zip(_active_sources, _active_weights)
                          if s not in ('audio', 'image')]

        _tasks: list[tuple[str, str]] = []
        for t in topics:
            seen_src: set[str] = set(_mm_guaranteed)
            for s in _mm_guaranteed:
                _tasks.append((t, s))
            # Fill remaining 3 slots from text sources
            remaining = max(0, 5 - len(_mm_guaranteed))
            if _text_sources and remaining > 0:
                picks = random.choices(_text_sources, weights=_text_weights or None, k=remaining)
                for s in picks:
                    if s not in seen_src:
                        seen_src.add(s)
                        _tasks.append((t, s))

        items_raw: list[dict] = []
        _fstatus_write(True)
        tc0 = time.perf_counter()
        try:
            futs = {self._search_pool.submit(_one_search, t, s): None for t, s in _tasks}
            for fut in as_completed(futs, timeout=st + 2):
                try:
                    for item in fut.result():
                        url = item.get('url', '')
                        if url and url not in self._known_urls and not _is_code_content(item):
                            items_raw.append(item)
                            self._known_urls.add(url)
                            self._known_urls_q.append(url)
                except Exception:
                    pass
        except Exception:
            log.debug('search phase timed out — partial results used')
        t_search = time.perf_counter() - tc0

        # Evict from set any URLs the deque dropped (maxlen overflow)
        if len(self._known_urls) > 50000:
            self._known_urls = set(self._known_urls_q)

        mm_items   = [i for i in items_raw if i.get('source') in ('image', 'audio')]
        text_items = [i for i in items_raw if i.get('source') not in ('image', 'audio')]

        # ── Phase 2: Two-stage fetch — IO and CPU overlap ─────────────────────
        # Stage A: HTTP download  (IO-bound, N_WORKERS_FETCH workers)
        # Stage B: HTML parse     (CPU-bound, half as many workers — trafilatura)
        # Stage A submits to Stage B as each download completes,
        # so parsing starts immediately instead of waiting for all downloads.
        from sources import fetch_raw, parse_raw

        def _stage_a(item: dict):
            try:
                return item, fetch_raw(item)
            except Exception:
                return item, None

        def _stage_b(item: dict, raw) -> dict | None:
            try:
                sents = parse_raw(item, raw)
            except Exception:
                sents = _sentences(item.get('snippet', ''), min_len=20)
            if len(sents) < mins:
                return None
            item['_sentences'] = sents[:mxs]
            return item

        fetched: list[dict] = []
        tf0 = time.perf_counter()

        # Persistent IO + CPU pools — no thread-create overhead per cycle
        try:
            io_futs  = {self._io_pool.submit(_stage_a, i): i for i in text_items}
            cpu_futs = {}
            for fut in as_completed(io_futs, timeout=ft + 2):
                try:
                    item, raw = fut.result()
                    cpu_futs[self._cpu_pool.submit(_stage_b, item, raw)] = item
                except Exception:
                    pass
            for fut in as_completed(cpu_futs, timeout=ft):
                try:
                    r = fut.result()
                    if r is not None:
                        fetched.append(r)
                except Exception:
                    pass
        except Exception:
            log.debug('fetch phase timed out — partial results used')

        t_fetch = time.perf_counter() - tf0

        # Signal live bar
        _active_goal = self._umber.dominant_goal() if self._umber is not None else ''
        if fetched:
            try:
                (_DATA / 'currently_reading.json').write_text(json.dumps({
                    'title':   fetched[0].get('title', '')[:160],
                    'source':  fetched[0].get('source', ''),
                    'topic':   fetched[0].get('topic', ''),
                    'url':     fetched[0].get('url', ''),
                    'n_items': len(fetched),
                    'goal':    _active_goal,
                    'ts':      datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
                }), encoding='utf-8')
            except Exception:
                pass

        # ── Phase 3: Dedup sentences, push to async encoder — non-blocking ────────
        # The encoder worker runs in a dedicated thread; the cycle never waits for
        # embeddings. Encode + index update + field ingest all happen in background.
        all_sents:  list[str]   = []
        boundaries: list[tuple] = []   # (start, end) into all_sents per article
        _seen_sents: set[str]   = set()
        for item in fetched:
            raw_sents = item['_sentences']
            deduped   = []
            for s in raw_sents:
                if s not in _seen_sents:
                    _seen_sents.add(s)
                    deduped.append(s)
            item['_sentences'] = deduped
            s_start = len(all_sents)
            all_sents.extend(deduped)
            boundaries.append((s_start, len(all_sents)))

        # Push batch to background encoder (deque maxlen=2 silently drops if full)
        if all_sents or mm_items:
            self._encode_q.put((all_sents, fetched, boundaries, list(mm_items)))

        t_extract = 0.0   # non-blocking — actual time is in background worker
        t_graph   = 0.0
        t_save    = 0.0
        n_ok      = len(fetched) + len(mm_items)

        _fstatus_write(False)

        # Record aggregate perf sample for this cycle
        with self._perf_lock:
            self._perf_samples.append({
                'fetch':    round(t_fetch,  3),
                'extract':  0.0,   # async — not blocking the cycle
                'detect':   0.0,
                'graph':    0.0,
                'save':     0.0,
                'subsys':   0.0,
                'total':    round(t_search + t_fetch, 3),
                'sentences': sum(len(item['_sentences']) for item in fetched),
                'concepts':  0,    # updated by background worker
                'source':    'cycle',
            })
            if len(self._perf_samples) > 50:
                self._perf_samples = self._perf_samples[-50:]

        # ── Field dynamics pass (Layer 3) ──────────────────────────────────────
        if self._field and self._dynamics and self._index:
            try:
                self._dynamics.propagate(self._field, self._index)
            except Exception as e:
                log.debug('dynamics error: %s', e)

        if self._field:
            try:
                self._field.decay()
            except Exception as e:
                log.debug('field decay error: %s', e)

        self._save_stats()

        # ── Growth log ─────────────────────────────────────────────────────────
        if self._index:
            try:
                line = json.dumps({
                    'ts':    datetime.now(tz=timezone.utc).isoformat(timespec='minutes'),
                    'nodes': self._index.node_count,
                    'edges': 0,
                })
                with open(_DATA / 'growth_log.jsonl', 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except Exception:
                pass

        # ── Periodic abstractor + worldview (every 10m) ────────────────────────
        checkin_every = _lcfg('checkin_every', CHECKIN_EVERY)
        if time.time() - self._last_checkin >= checkin_every:
            try:
                from abstractor import Abstractor
                Abstractor.get().run()
                self._last_checkin = time.time()
                log.info('abstractor refresh complete')
            except Exception as e:
                log.debug('abstractor error: %s', e)

            if self._umber is not None:
                try:
                    snap = self._umber.worldview_snapshot()
                    _wv  = _DATA / 'worldview.json'
                    tmp  = _wv.with_suffix('.tmp')
                    tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding='utf-8')
                    tmp.replace(_wv)
                except Exception as e:
                    log.debug('worldview→umber error: %s', e)

            # ── Temporal Compression — compress episodic memory into identity arcs
            try:
                from chronicle import Chronicle
                Chronicle.get().compress(field=self._field)
            except Exception as e:
                log.debug('chronicle error: %s', e)

        self._feed_flush()

        self._write_perf_profile({
            't_search_s':  round(t_search, 2),
            't_process_s': round(t_fetch,  2),
            't_ticks_s':   0.0,
            't_total_s':   round(t_search + t_fetch, 2),
            'items_found': len(items_raw),
            'items_ok':    n_ok,
            'workers':     nws,
        })

    # ── Start ──────────────────────────────────────────────────────────────────

    def start(self, graph=None) -> None:
        if self._thread and self._thread.is_alive():
            return

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
        threading.Thread(target=self._subsys_worker,    daemon=True, name='subsys').start()
        threading.Thread(target=self._regulator_worker, daemon=True, name='regulator').start()
        threading.Thread(target=self._umber_worker,     daemon=True, name='umber').start()
        # Multiple encoder workers — 4 parallel threads drain the queue fast
        n_enc = _lcfg('n_encoder_workers', N_ENCODER_WORKERS)
        for i in range(max(1, n_enc)):
            threading.Thread(target=self._encoder_worker, daemon=True, name=f'encoder-{i}').start()
        threading.Thread(target=self._prefetch_worker,  daemon=True, name='prefetch').start()
        threading.Thread(target=_watchdog,              daemon=True, name='watchdog').start()
        try:
            from autonomous import start_all
            start_all()
        except Exception as _ae:
            log.debug('autonomous layer not started: %s', _ae)
