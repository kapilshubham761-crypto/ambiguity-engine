"""
Node [W] — World Model Formation
=================================
V3 Step 10: Causal edge registry — the engine builds a model of
how concepts relate causally, not just co-occur.

Relations:  causes | suppresses | predicts | depends_on

Each (source, target, relation) triple gets a confidence score that
decays with every new observation (older evidence fades).  When two
concepts co-transition frequently in a directed manner, the system
infers a causal link.

Usage:
    from world_model import WorldModel
    wm = WorldModel.get()
    wm.observe('stress', 'cortisol', 'causes')
    effects = wm.predict_effects('stress')
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('world_model')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'world_model.json')

_SINGLETON: Optional['WorldModel'] = None

VALID_RELATIONS = {'causes', 'suppresses', 'predicts', 'depends_on'}


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('world_model', {})
    except Exception:
        return {}


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# WorldModel                                                                   #
# --------------------------------------------------------------------------- #

class WorldModel:
    """
    Maintains a directed causal graph over concepts.
    Edges have a relation type and a confidence score.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._confidence_decay = float(cfg.get('confidence_decay', 0.98))
        self._min_confidence   = float(cfg.get('min_confidence',   0.05))
        self._max_edges        = int(cfg.get('max_edges',          2000))
        self._relations        = list(cfg.get('relations', list(VALID_RELATIONS)))

        # edges[(source, target, relation)] = confidence
        self._edges: dict[tuple, float] = {}
        self._obs_count: int = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            for item in raw.get('edges', []):
                key = (item['source'], item['target'], item['relation'])
                self._edges[key] = float(item['confidence'])
            self._obs_count = int(raw.get('obs_count', 0))
        except Exception:
            pass

    def _save(self) -> None:
        edges_list = [
            {'source': s, 'target': t, 'relation': r, 'confidence': round(c, 6)}
            for (s, t, r), c in self._edges.items()
        ]
        _atomic_write(_DATA_PATH, {
            'edges': edges_list,
            'obs_count': self._obs_count,
            'ts': time.time(),
        })

    # ------------------------------------------------------------------ #
    # Core API                                                             #
    # ------------------------------------------------------------------ #

    def observe(self, source: str, target: str, relation: str,
                strength: float = 1.0) -> None:
        """
        Record a causal observation.  Confidence of existing edges decays
        slightly each call; the observed edge is reinforced.
        """
        if relation not in VALID_RELATIONS:
            return
        if source == target:
            return

        self._obs_count += 1

        # Decay all edges slightly
        for k in list(self._edges):
            self._edges[k] *= self._confidence_decay
            if self._edges[k] < self._min_confidence:
                del self._edges[k]

        # Reinforce observed edge
        key = (source, target, relation)
        current = self._edges.get(key, 0.0)
        self._edges[key] = min(1.0, current + (1.0 - current) * 0.30 * strength)

        # Prune to max_edges (drop weakest)
        if len(self._edges) > self._max_edges:
            sorted_keys = sorted(self._edges, key=lambda k: self._edges[k])
            for k in sorted_keys[:len(self._edges) - self._max_edges]:
                del self._edges[k]

        if self._obs_count % 50 == 0:
            self._save()
        log.debug('world_model: observed %s -[%s]-> %s', source, relation, target)

    def predict_effects(self, concept: str,
                        min_confidence: float = 0.10) -> list[dict]:
        """
        Return all outgoing causal edges from concept above min_confidence.
        Sorted by confidence descending.
        """
        results = []
        for (s, t, r), c in self._edges.items():
            if s == concept and c >= min_confidence:
                results.append({'target': t, 'relation': r, 'confidence': round(c, 4)})
        results.sort(key=lambda x: x['confidence'], reverse=True)
        return results

    def predict_causes(self, concept: str,
                       min_confidence: float = 0.10) -> list[dict]:
        """Return all incoming causal edges pointing TO concept."""
        results = []
        for (s, t, r), c in self._edges.items():
            if t == concept and c >= min_confidence:
                results.append({'source': s, 'relation': r, 'confidence': round(c, 4)})
        results.sort(key=lambda x: x['confidence'], reverse=True)
        return results

    def infer_from_context(self, concepts: list[str]) -> None:
        """
        Auto-observe plausible causal edges from a co-occurring concept set.
        Uses transition graph if available to determine direction.
        """
        if len(concepts) < 2:
            return
        try:
            from episodes import EpisodeStore
            ep = EpisodeStore.get()
            for i, a in enumerate(concepts):
                for b in concepts[i+1:]:
                    ta = ep.transitions_from(a).get(b, 0.0)
                    tb = ep.transitions_from(b).get(a, 0.0)
                    if ta > tb and ta > 0.1:
                        self.observe(a, b, 'predicts', strength=min(ta, 1.0))
                    elif tb > ta and tb > 0.1:
                        self.observe(b, a, 'predicts', strength=min(tb, 1.0))
        except Exception:
            pass

    def top_edges(self, n: int = 20) -> list[dict]:
        """Return n highest-confidence edges."""
        sorted_edges = sorted(self._edges.items(), key=lambda x: x[1], reverse=True)
        return [
            {'source': s, 'target': t, 'relation': r, 'confidence': round(c, 4)}
            for (s, t, r), c in sorted_edges[:n]
        ]

    def snapshot(self) -> dict:
        return {
            'edge_count':  len(self._edges),
            'obs_count':   self._obs_count,
            'top_edges':   self.top_edges(10),
        }

    def flush(self) -> None:
        self._save()

    @classmethod
    def get(cls) -> 'WorldModel':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
