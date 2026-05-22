"""
Node [M] — Meta-State Dynamics
================================
Tracks the engine's ephemeral internal state — the psychological layer
that sits above the graph but below the prompt.

Five dimensions, updated on every concept-accept event:

  mood        — overall temperament  (curious / conflicted / focused /
                                      drifting / saturated / exploring)
  tensions    — concept pairs held in active structural conflict
  attractors  — unstable hubs pulling new associations toward them
  drift       — shift in semantic centre-of-mass between context windows
  momentum    — per-concept activation inertia (decays each update)

Public API
----------
MetaStateEngine.get()          singleton accessor
  .on_accept(texts, graph)  →  MetaState   update + persist
  .current()                →  MetaState | None
  .snapshot()               →  dict (JSON-safe)

MOOD_META   dict   {mood: (emoji, hex_colour, description)}
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from logger import get_logger

log = get_logger('meta_state')

_ROOT   = os.path.join(os.path.dirname(__file__), '..')
_PATH   = os.path.join(_ROOT, 'data', 'meta_state.json')
_SCORE_LOG = os.path.join(_ROOT, 'logs', 'ambiguity_scores.jsonl')

_CONTEXT_SIZE = 60    # rolling window length
_DECAY        = 0.82  # momentum multiplier per accept event
_TENSION_LO   = 0.12  # edge-weight lower bound for the tension zone
_TENSION_HI   = 0.68  # edge-weight upper bound for the tension zone


# ──────────────────────────────────────────────────────────── Mood meta ──

MOOD_META: dict[str, tuple[str, str, str]] = {
    'curious':    ('🔍', '#4FC3F7', 'Actively exploring new territory'),
    'conflicted': ('⚡', '#FF7043', 'Competing concepts held in tension'),
    'focused':    ('🎯', '#66BB6A', 'Deep attention on a tight concept cluster'),
    'drifting':   ('🌊', '#78909C', 'Low activation — semantic centre unfixed'),
    'saturated':  ('🌕', '#FFA726', 'Near stage capacity — growth slowing'),
    'exploring':  ('🧭', '#AB47BC', 'Steady mixed-state — no dominant pull'),
}


# ─────────────────────────────────────────────────────────── Data class ──

@dataclass
class MetaState:
    timestamp:    str
    mood:         str
    mood_intensity: float        # 0–1

    tensions:       list[dict]   # top-5 [{concept_a, concept_b, strength, edge_weight}]
    attractors:     list[dict]   # top-5 [{concept, pull, degree, momentum}]
    momentum:       dict         # {concept_text: float} all non-zero values
    context_window: list[str]    # last N concept texts (newest last)
    drift_in:       list[str]    # concepts moving INTO focus
    drift_out:      list[str]    # concepts moving OUT OF focus

    active_concept_count: int    # concepts with momentum > 0.15
    avg_momentum:         float
    recent_growth:        int    # node delta since last update


# ──────────────────────────────────────────────────────── Helpers ──

def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec='seconds')


def _jaccard(a: set, b: set) -> float:
    u = len(a | b)
    return len(a & b) / u if u else 1.0


def _read_recent_scores(n: int = 100) -> list[float]:
    scores = []
    if not os.path.exists(_SCORE_LOG):
        return scores
    try:
        with open(_SCORE_LOG, encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines[-n:]:
            e = json.loads(line)
            scores.append(float(e['score']))
    except Exception:
        pass
    return scores


def _read_stage() -> int:
    try:
        p = os.path.join(_ROOT, 'data', 'discovery_stage.json')
        return int(json.load(open(p, encoding='utf-8')).get('stage', 0))
    except Exception:
        return 0


# ─────────────────────────────────────────────────── Five-dimension calc ──

def _tensions(graph, top_n: int = 5) -> list[dict]:
    """
    Mid-weight edges whose endpoints have dissimilar neighborhoods.
    These concepts are associated but pulling in different semantic directions.
    """
    results = []
    degrees = dict(graph._g.degree())
    max_deg = max(degrees.values(), default=1)

    for u, v, data in graph._g.edges(data=True):
        w = data.get('weight', 0.0)
        if not (_TENSION_LO < w < _TENSION_HI):
            continue

        nbrs_u = set(graph._g.neighbors(u)) - {v}
        nbrs_v = set(graph._g.neighbors(v)) - {u}
        dissim = 1.0 - _jaccard(nbrs_u, nbrs_v)

        deg_factor = (degrees[u] + degrees[v]) / (2 * max_deg)
        # w_factor peaks at 0.40 (halfway through tension zone)
        w_factor   = 1.0 - abs(w - 0.40) / 0.40

        strength = round(dissim * w_factor * (0.4 + 0.6 * deg_factor), 4)
        if strength < 0.05:
            continue

        results.append({
            'concept_a':   graph._g.nodes[u].get('text', u),
            'concept_b':   graph._g.nodes[v].get('text', v),
            'strength':    strength,
            'edge_weight': round(w, 4),
        })

    results.sort(key=lambda x: x['strength'], reverse=True)
    return results[:top_n]


def _attractors(graph, momentum: dict, top_n: int = 5) -> list[dict]:
    """
    Pull = momentum × log(1 + degree).
    High pull → concept is both recently active AND highly connected.
    """
    results = []
    for nid, data in graph._g.nodes(data=True):
        text = data.get('text', nid)
        mom  = momentum.get(text, 0.0)
        deg  = graph._g.degree(nid)
        pull = round(mom * math.log1p(deg), 4)
        if pull < 0.02:
            continue
        results.append({
            'concept':  text,
            'pull':     pull,
            'degree':   deg,
            'momentum': round(mom, 4),
        })

    results.sort(key=lambda x: x['pull'], reverse=True)
    return results[:top_n]


def _drift(window: list[str]) -> tuple[list[str], list[str]]:
    """Split window in half; concepts that moved in or out of focus."""
    if len(window) < 4:
        return [], []
    mid     = len(window) // 2
    old_ct  = Counter(window[:mid])
    new_ct  = Counter(window[mid:])
    drift_in  = sorted((c for c in new_ct if new_ct[c] > old_ct.get(c, 0)),
                       key=lambda c: new_ct[c] - old_ct.get(c, 0), reverse=True)[:5]
    drift_out = sorted((c for c in old_ct if old_ct[c] > new_ct.get(c, 0)),
                       key=lambda c: old_ct[c] - new_ct.get(c, 0), reverse=True)[:5]
    return drift_in, drift_out


def _mood(graph, momentum: dict, tensions: list[dict],
          prev_nodes: int) -> tuple[str, float]:
    """Rule-based mood from graph + momentum + ambiguity spread."""
    import numpy as np

    scores   = _read_recent_scores()
    spread   = float(np.std(scores)) if len(scores) > 1 else 0.0
    mom_vals = list(momentum.values())
    active   = [m for m in mom_vals if m > 0.15]
    avg_mom  = float(np.mean(mom_vals)) if mom_vals else 0.0
    growth   = max(graph.node_count - prev_nodes, 0)

    try:
        from curriculum import STAGE_TARGETS
        stage        = _read_stage()
        target_nodes = STAGE_TARGETS.get(stage, STAGE_TARGETS[7])['nodes']
        node_pct     = graph.node_count / max(target_nodes, 1)
    except Exception:
        node_pct = 0.0

    # Most-specific rules first
    if spread > 0.22:
        return 'conflicted', min(spread / 0.30, 1.0)
    if 2 <= len(active) <= 8 and avg_mom > 0.6:
        return 'focused', min(avg_mom / 2.0, 1.0)
    if growth >= 12:
        return 'curious', min(growth / 25.0, 1.0)
    if len(active) <= 1:
        return 'drifting', 0.7
    if node_pct >= 0.85:
        return 'saturated', min(node_pct, 1.0)
    return 'exploring', 0.5


# ──────────────────────────────────────────────────────────── Engine ──

class MetaStateEngine:
    """
    Singleton. Holds ephemeral state in memory and mirrors it to
    data/meta_state.json for cross-process reads.

    Usage:
        engine = MetaStateEngine.get()
        state  = engine.on_accept(['gravity', 'mass', 'orbit'], graph)
    """

    _instance: MetaStateEngine | None = None

    def __init__(self) -> None:
        self._state: MetaState | None = None
        self._prev_node_count: int = 0
        self._load()

    @classmethod
    def get(cls) -> MetaStateEngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── public ──────────────────────────────────────────────────────────

    def on_accept(self, concept_texts: list[str], graph) -> MetaState:
        """
        Call after graph.update() with the list of concept texts just fed in.
        Decays existing momentum, boosts activated concepts, recomputes all
        five dimensions, persists, and returns the new MetaState.
        """
        import numpy as np

        mom = dict(self._state.momentum)       if self._state else {}
        ctx = list(self._state.context_window) if self._state else []

        # 1 — Decay all momentum
        for k in list(mom.keys()):
            mom[k] *= _DECAY
            if mom[k] < 0.01:
                del mom[k]

        # 2 — Boost newly activated concepts
        for text in concept_texts:
            mom[text] = mom.get(text, 0.0) + 1.0

        # 3 — Advance context window
        ctx.extend(concept_texts)
        ctx = ctx[-_CONTEXT_SIZE:]

        # 4 — Recompute all five dimensions
        t_list             = _tensions(graph)
        a_list             = _attractors(graph, mom)
        drift_in, drift_out = _drift(ctx)
        mood, intensity    = _mood(graph, mom, t_list, self._prev_node_count)

        mom_vals   = list(mom.values())
        avg_mom    = float(np.mean(mom_vals)) if mom_vals else 0.0
        active_ct  = sum(1 for m in mom_vals if m > 0.15)
        growth     = max(graph.node_count - self._prev_node_count, 0)

        self._state = MetaState(
            timestamp=_now(),
            mood=mood,
            mood_intensity=round(intensity, 3),
            tensions=t_list,
            attractors=a_list,
            momentum=mom,
            context_window=ctx,
            drift_in=drift_in,
            drift_out=drift_out,
            active_concept_count=active_ct,
            avg_momentum=round(avg_mom, 4),
            recent_growth=growth,
        )
        self._prev_node_count = graph.node_count
        self._save()
        log.debug('meta-state → mood=%s (%.2f)  active=%d  tensions=%d',
                  mood, intensity, active_ct, len(t_list))
        return self._state

    def current(self) -> MetaState | None:
        return self._state

    def snapshot(self) -> dict:
        return asdict(self._state) if self._state else {}

    # ── persistence ─────────────────────────────────────────────────────

    def _save(self) -> None:
        if self._state is None:
            return
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        try:
            with open(_PATH, encoding='utf-8') as f:
                d = json.load(f)
            self._state = MetaState(**{k: d[k] for k in MetaState.__dataclass_fields__})
        except Exception:
            self._state = None
