"""
Node [AI] — Active Inference
==============================
V3 Step 13: The engine decides what to pay attention to next.

Instead of passive concept reception, the system actively steers
toward information that reduces uncertainty.

Three triggers:
    uncertainty > 0.65 → seek information (target: high-tension concept)
    entropy < 0.20     → inject novelty (target: random novel concept)
    open contradictions > 5 → explore related region

Each trigger returns a seek_action:
    {type, target, reason, urgency}

Usage:
    from active_inference import ActiveInference
    ai = ActiveInference.get()
    action = ai.step()   # returns dict or None
"""

from __future__ import annotations

import random
from typing import Optional

import yaml
import os

from logger import get_logger

log = get_logger('active_inference')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['ActiveInference'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('active_inference', {})
    except Exception:
        return {}


class ActiveInference:
    """
    Evaluates system state and returns the most pressing seek action.
    Pure read-only — never modifies other subsystems directly.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._uncertainty_threshold    = float(cfg.get('uncertainty_threshold',    0.65))
        self._entropy_low_threshold    = float(cfg.get('entropy_low_threshold',    0.20))
        self._contradiction_threshold  = int(cfg.get('contradiction_threshold',    5))
        self._last_action: dict | None = None
        self._action_count: int        = 0

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _uncertainty(self) -> float:
        """Mean tension across hot concepts — proxy for uncertainty."""
        try:
            from tension import TensionTracker
            tt = TensionTracker.get()
            hot = tt.hot_concepts()
            if not hot:
                return 0.0
            return sum(tt.mean(c) for c in hot) / len(hot)
        except Exception:
            return 0.0

    def _entropy(self) -> float:
        try:
            from stability import StabilityMonitor
            return StabilityMonitor.get().entropy()
        except Exception:
            return 0.5

    def _open_contradictions(self) -> int:
        try:
            from contradiction import ContradictionRegistry
            return len(ContradictionRegistry.get().unresolved())
        except Exception:
            return 0

    def _high_tension_concept(self) -> str | None:
        try:
            from tension import TensionTracker
            tt = TensionTracker.get()
            hot = tt.hot_concepts()
            if hot:
                return max(hot, key=lambda c: tt.mean(c))
        except Exception:
            pass
        return None

    def _novel_concept(self) -> str | None:
        try:
            from novelty import NoveltyTracker
            from meta_state import MetaState
            nt = NoveltyTracker.get()
            meta = MetaState.get()
            active = list(meta.active(threshold=0.0).keys())
            if active:
                return max(active, key=lambda c: nt.novelty_score(c))
        except Exception:
            pass
        return None

    def _contradiction_concept(self) -> str | None:
        try:
            from contradiction import ContradictionRegistry
            unresolved = ContradictionRegistry.get().unresolved()
            if unresolved:
                c = unresolved[0]
                return c.get('concept_a')
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    # Main step                                                            #
    # ------------------------------------------------------------------ #

    def step(self) -> dict | None:
        """
        Evaluate current state and return the most urgent seek action,
        or None if no action is warranted.
        """
        actions = []

        # 1. High uncertainty → information seeking
        unc = self._uncertainty()
        if unc >= self._uncertainty_threshold:
            target = self._high_tension_concept()
            if target:
                actions.append({
                    'type':    'seek_information',
                    'target':  target,
                    'reason':  f'uncertainty={unc:.2f} exceeds threshold',
                    'urgency': unc,
                })

        # 2. Low entropy → inject novelty
        ent = self._entropy()
        if ent < self._entropy_low_threshold:
            target = self._novel_concept()
            if target:
                actions.append({
                    'type':    'inject_novelty',
                    'target':  target,
                    'reason':  f'entropy={ent:.2f} below threshold',
                    'urgency': 1.0 - ent,
                })

        # 3. Too many contradictions → explore
        n_contra = self._open_contradictions()
        if n_contra >= self._contradiction_threshold:
            target = self._contradiction_concept()
            if target:
                actions.append({
                    'type':    'explore_contradiction',
                    'target':  target,
                    'reason':  f'{n_contra} open contradictions',
                    'urgency': min(1.0, n_contra / 10.0),
                })

        if not actions:
            return None

        # Return highest urgency action
        action = max(actions, key=lambda a: a['urgency'])
        self._last_action  = action
        self._action_count += 1
        log.debug('active_inference: %s → %s (urgency=%.2f)',
                  action['type'], action['target'], action['urgency'])
        return action

    def last_action(self) -> dict | None:
        return self._last_action

    def snapshot(self) -> dict:
        return {
            'action_count': self._action_count,
            'last_action':  self._last_action,
            'uncertainty':  round(self._uncertainty(), 4),
            'entropy':      round(self._entropy(), 4),
            'contradictions': self._open_contradictions(),
        }

    @classmethod
    def get(cls) -> 'ActiveInference':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
