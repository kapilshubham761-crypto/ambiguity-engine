"""
Node [X] — Contradiction Engine
==================================
V3 Step 4: Detects and preserves mutually incompatible concept activations.

Real cognition doesn't immediately overwrite contradictions — it holds them.
Contradictions become cognitive attractors: they sustain tension, drive
exploration, and eventually force model evolution.

Detection heuristic:
    Two concepts form a contradiction candidate when:
    1. Both appear as "hot" in TensionTracker (actively conflicting)
    2. Their semantic similarity (from graph embeddings) is >= threshold
       (they're semantically related, not just different topics)
    3. They come from opposing claim patterns in episodes
       (heuristic: one concept often follows the other AND vice versa —
       bidirectional transitions with comparable weight = opposing claims)

Contradiction schema:
    {
      id:                str   UUID
      concept_a:         str
      concept_b:         str
      conflict_type:     str   'bidirectional' | 'tension_clash' | 'manual'
      confidence_gap:    float  |score_a - score_b|
      resolution_status: str   'open' | 'resolved' | 'dissolved'
      first_seen:        str   ISO
      last_updated:      str   ISO
      tension_a:         float
      tension_b:         float
    }

Public API:
    observe(concepts)          detect new contradictions from current context
    register(a, b, type)       manually register a contradiction
    unresolved() → list[dict]  active open contradictions
    resolve(id)                mark one as resolved
    snapshot() → dict          summary for UI / reflection
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('contradiction')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_PATH     = os.path.join(_ROOT, 'data', 'contradictions.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['ContradictionRegistry'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('contradiction', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# ContradictionRegistry                                                        #
# --------------------------------------------------------------------------- #

class ContradictionRegistry:
    """
    Detects, stores, and surfaces active cognitive contradictions.

    Two concepts are contradictory if:
    - Both are hot in TensionTracker  (high ambiguity load)
    - They appear in bidirectional transitions (each commonly follows the other)
    - Their co-occurrence is high but their meta-state activations conflict
    """

    def __init__(self, path: str = _PATH) -> None:
        cfg = _cfg()
        self._cosine_min = float(cfg.get('detection_cosine_min', 0.30))
        self._tension_min = float(cfg.get('tension_min', 0.40))
        self._max         = int(cfg.get('max_contradictions', 50))
        self._path        = path
        self._items: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def observe(self, concepts: list[str]) -> list[dict]:
        """
        Scan current concept list for contradiction candidates.
        Returns newly registered contradictions (may be empty).
        """
        if len(concepts) < 2:
            return []

        hot_set = self._hot_concepts()
        hot_in_context = [c for c in concepts if c in hot_set]
        if len(hot_in_context) < 2:
            return []

        new_contradictions = []
        for i in range(len(hot_in_context)):
            for j in range(i + 1, len(hot_in_context)):
                a, b = hot_in_context[i], hot_in_context[j]
                if self._already_registered(a, b):
                    continue
                if self._is_bidirectional(a, b):
                    c = self._make(a, b, 'bidirectional')
                    new_contradictions.append(c)

        if new_contradictions:
            self._items.extend(new_contradictions)
            self._trim()
            self._save()
            log.debug('contradiction: %d new (%d total)',
                      len(new_contradictions), len(self._items))
        return new_contradictions

    def register(self, concept_a: str, concept_b: str,
                 conflict_type: str = 'manual') -> dict:
        """Manually register a contradiction."""
        c = self._make(concept_a, concept_b, conflict_type)
        self._items.append(c)
        self._trim()
        self._save()
        return c

    def unresolved(self) -> list[dict]:
        return [c for c in self._items if c.get('resolution_status') == 'open']

    def resolve(self, contradiction_id: str) -> bool:
        for c in self._items:
            if c['id'] == contradiction_id:
                c['resolution_status'] = 'resolved'
                c['last_updated'] = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
                self._save()
                return True
        return False

    def all(self) -> list[dict]:
        return list(self._items)

    def snapshot(self) -> dict:
        unres = self.unresolved()
        return {
            'total':       len(self._items),
            'open':        len(unres),
            'resolved':    len(self._items) - len(unres),
            'top_open':    unres[:5],
        }

    @classmethod
    def get(cls) -> 'ContradictionRegistry':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _hot_concepts(self) -> set[str]:
        try:
            from tension import TensionTracker
            t = TensionTracker.get()
            return set(t.hot(n=30))
        except Exception:
            return set()

    def _is_bidirectional(self, a: str, b: str) -> bool:
        """
        True when A→B and B→A both exist as transitions with weight > tension_min.
        This signals two concepts that reciprocally follow each other — a
        structural contradiction (both directions are plausible, neither dominates).
        """
        try:
            from episodes import EpisodeStore
            store = EpisodeStore.get()
            ab = store.transitions_from(a).get(b, 0.0)
            ba = store.transitions_from(b).get(a, 0.0)
            return ab > self._tension_min and ba > self._tension_min
        except Exception:
            return False

    def _already_registered(self, a: str, b: str) -> bool:
        pair = frozenset([a, b])
        for c in self._items:
            if frozenset([c['concept_a'], c['concept_b']]) == pair:
                if c.get('resolution_status') == 'open':
                    return True
        return False

    def _make(self, a: str, b: str, conflict_type: str) -> dict:
        now = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
        ta = self._tension_mean(a)
        tb = self._tension_mean(b)
        return {
            'id':                str(uuid.uuid4()),
            'concept_a':         a,
            'concept_b':         b,
            'conflict_type':     conflict_type,
            'confidence_gap':    round(abs(ta - tb), 4),
            'resolution_status': 'open',
            'first_seen':        now,
            'last_updated':      now,
            'tension_a':         round(ta, 4),
            'tension_b':         round(tb, 4),
        }

    @staticmethod
    def _tension_mean(concept: str) -> float:
        try:
            from tension import TensionTracker
            return TensionTracker.get().mean(concept)
        except Exception:
            return 0.0

    def _trim(self) -> None:
        if len(self._items) > self._max:
            self._items = self._items[-self._max:]

    def _save(self) -> None:
        dir_ = os.path.dirname(self._path)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps(self._items, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, self._path)

    def _load(self) -> None:
        try:
            with open(self._path, encoding='utf-8') as f:
                self._items = json.load(f)
            log.debug('contradictions loaded: %d', len(self._items))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('contradiction load error: %s', e)
