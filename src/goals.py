"""
Node [Go] — Goal Formation Engine
=====================================
V3 Step 8: Emergent directional behavior through intrinsic drives.

The engine does not follow hardcoded tasks. Instead, at each cycle it
evaluates five competing drives and selects the most urgent one.

Five intrinsic drives:
    reduce_uncertainty    → high ambiguity load / many open contradictions
    increase_novelty      → low novelty balance / repetition detected
    resolve_contradiction → many unresolved contradictions
    maintain_stability    → entropy out of healthy band
    expand_regions        → single dominant region for too long

Goal selection formula:
    score(drive) = drive_weight × signal_strength(drive)

    where signal_strength is computed from live cognitive metrics:
      reduce_uncertainty    = ambiguity_load (from TensionTracker)
      increase_novelty      = 1 - novelty_balance (from NoveltyTracker)
      resolve_contradiction = open_contradictions / max_contradictions
      maintain_stability    = |entropy - 0.5| × 2   (distance from ideal midpoint)
      expand_regions        = 1 / max(region_count, 1)

    goal = argmax(score)

The selected goal is exposed to:
    - ReflectionMonitor [Rf] — for self-reporting
    - HomeworkTracker [H] — for goal-biased topic selection (future)
    - Teacher [D] — for logging / debugging

Public API:
    current_goal() → str          e.g. "increase_novelty"
    drive_scores()  → dict        all drive scores (for UI)
    update()        → dict        recompute and return
    snapshot()      → dict
"""

from __future__ import annotations

import os
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('goals')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['GoalEngine'] = None

_DEFAULT_DRIVES = {
    'reduce_uncertainty':    0.30,
    'increase_novelty':      0.25,
    'resolve_contradiction': 0.20,
    'maintain_stability':    0.15,
    'expand_regions':        0.10,
}


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('goals', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# GoalEngine                                                                   #
# --------------------------------------------------------------------------- #

class GoalEngine:
    """
    Evaluates intrinsic drives and selects the currently dominant goal.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._weights     = cfg.get('drives', _DEFAULT_DRIVES)
        self._update_every = int(cfg.get('update_every', 10))
        self._tick_count   = 0
        self._goal:   str         = 'maintain_stability'
        self._scores: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def current_goal(self) -> str:
        return self._goal

    def drive_scores(self) -> dict[str, float]:
        return dict(self._scores)

    def tick(self) -> str:
        """
        Call every background cycle.
        Only recomputes every update_every ticks to avoid overhead.
        """
        self._tick_count += 1
        if self._tick_count % self._update_every == 0:
            self.update()
        return self._goal

    def update(self) -> dict:
        """Force recompute goal from current cognitive state."""
        signals = self._compute_signals()
        scores  = {
            drive: round(float(self._weights.get(drive, 0.0)) * signals.get(drive, 0.0), 4)
            for drive in self._weights
        }
        self._scores = scores
        if scores:
            best = max(scores, key=lambda d: scores[d])
            if best != self._goal:
                log.debug('goal shift: %s → %s  scores=%s', self._goal, best, scores)
            self._goal = best
        return dict(self._scores)

    def snapshot(self) -> dict:
        return {
            'current_goal': self._goal,
            'drive_scores': self._scores,
            'tick_count':   self._tick_count,
        }

    @classmethod
    def get(cls) -> 'GoalEngine':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Signal computation                                                   #
    # ------------------------------------------------------------------ #

    def _compute_signals(self) -> dict[str, float]:
        return {
            'reduce_uncertainty':    self._signal_uncertainty(),
            'increase_novelty':      self._signal_novelty(),
            'resolve_contradiction': self._signal_contradiction(),
            'maintain_stability':    self._signal_stability(),
            'expand_regions':        self._signal_regions(),
        }

    @staticmethod
    def _signal_uncertainty() -> float:
        try:
            from tension import TensionTracker
            tt  = TensionTracker.get()
            hot = tt.hot(n=10)
            if not hot:
                return 0.0
            return round(sum(tt.mean(c) for c in hot) / len(hot), 4)
        except Exception:
            return 0.0

    @staticmethod
    def _signal_novelty() -> float:
        try:
            from memory import TemporalMemory
            from novelty import NoveltyTracker
            nov     = NoveltyTracker.get()
            working = TemporalMemory.get().top_working(20)
            if not working:
                return 0.5
            novel = sum(1 for c, _ in working if nov.times_seen(c) <= 2)
            nov_balance = novel / len(working)
            return round(1.0 - nov_balance, 4)   # low novelty = high signal
        except Exception:
            return 0.5

    @staticmethod
    def _signal_contradiction() -> float:
        try:
            from contradiction import ContradictionRegistry
            snap = ContradictionRegistry.get().snapshot()
            open_ = snap.get('open', 0)
            return round(min(open_ / 10.0, 1.0), 4)
        except Exception:
            return 0.0

    @staticmethod
    def _signal_stability() -> float:
        try:
            from stability import StabilityMonitor
            sm = StabilityMonitor.get()
            h  = sm.entropy()
            return round(abs(h - 0.50) * 2.0, 4)    # 0 at perfect midpoint
        except Exception:
            return 0.0

    @staticmethod
    def _signal_regions() -> float:
        try:
            from meta_state import MetaState
            snap = MetaState.get().snapshot()
            n    = max(snap.get('region_count', 1), 1)
            return round(1.0 / n, 4)                 # more regions = less urgency
        except Exception:
            return 1.0
