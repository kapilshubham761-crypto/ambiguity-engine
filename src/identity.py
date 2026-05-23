"""
Node [Id] — Identity Persistence
==================================
V3 Step 19: The engine accumulates a stable personality over time.

Five cognitive traits, each drifting slowly based on recent behavior:
    exploration_style      0=conservative  1=aggressive explorer
    novelty_bias           0=familiarity   1=novelty seeker
    stability_bias         0=chaotic       1=stability seeker
    abstraction_depth      0=concrete      1=abstract thinker
    contradiction_tolerance 0=avoid        1=embrace

Traits drift by drift_rate=0.02 toward the current behavioral signal.
This creates a persistent cognitive character that evolves slowly.

Usage:
    from identity import IdentityTracker
    it = IdentityTracker.get()
    it.observe()   # call periodically to update traits
    traits = it.traits()
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('identity')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'identity.json')

_SINGLETON: Optional['IdentityTracker'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('identity', {})
    except Exception:
        return {}


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


class IdentityTracker:
    """
    Maintains slowly-drifting cognitive trait profile.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._drift_rate = float(cfg.get('drift_rate', 0.02))
        default_traits   = cfg.get('traits', {})

        self._traits: dict[str, float] = {
            'exploration_style':       float(default_traits.get('exploration_style',       0.50)),
            'novelty_bias':            float(default_traits.get('novelty_bias',            0.50)),
            'stability_bias':          float(default_traits.get('stability_bias',          0.50)),
            'abstraction_depth':       float(default_traits.get('abstraction_depth',       0.50)),
            'contradiction_tolerance': float(default_traits.get('contradiction_tolerance', 0.50)),
        }
        self._observe_count: int = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            saved = raw.get('traits', {})
            for k in self._traits:
                if k in saved:
                    self._traits[k] = float(saved[k])
            self._observe_count = int(raw.get('observe_count', 0))
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'traits':        {k: round(v, 4) for k, v in self._traits.items()},
            'observe_count': self._observe_count,
            'ts':            time.time(),
        })

    # ------------------------------------------------------------------ #
    # Signal extraction                                                    #
    # ------------------------------------------------------------------ #

    def _behavioral_signals(self) -> dict[str, float]:
        """
        Derive target signals for each trait from current system state.
        Each signal is in [0, 1].
        """
        signals: dict[str, float] = {}

        try:
            from novelty import NoveltyTracker
            from meta_state import MetaState
            nt   = NoveltyTracker.get()
            meta = MetaState.get()
            active = list(meta.active(threshold=0.0).keys())
            if active:
                avg_novelty = sum(nt.novelty_score(c) for c in active[:20]) / min(len(active), 20)
                signals['novelty_bias'] = avg_novelty
        except Exception:
            pass

        try:
            from stability import StabilityMonitor
            sm  = StabilityMonitor.get()
            ent = sm.entropy()
            signals['exploration_style'] = ent
            # High stability bias → low entropy preference
            signals['stability_bias'] = 1.0 - ent
        except Exception:
            pass

        try:
            from abstractor import Abstractor
            ab = Abstractor.get()
            snp = ab.snapshot()
            count = snp.get('abstract_count', 0)
            # More abstractions → higher abstraction depth signal
            signals['abstraction_depth'] = min(1.0, count / 50.0)
        except Exception:
            pass

        try:
            from contradiction import ContradictionRegistry
            cr = ContradictionRegistry.get()
            n_contra = len(cr.unresolved())
            # Engine that resolves contradictions has high tolerance
            signals['contradiction_tolerance'] = min(1.0, n_contra / 10.0)
        except Exception:
            pass

        return signals

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def observe(self) -> None:
        """Update traits by drifting toward current behavioral signals."""
        signals = self._behavioral_signals()
        self._observe_count += 1

        for trait, target in signals.items():
            if trait in self._traits:
                current = self._traits[trait]
                self._traits[trait] = round(
                    current + self._drift_rate * (target - current), 4)

        if self._observe_count % 5 == 0:
            self._save()
            log.debug('identity: traits updated (obs=%d)', self._observe_count)

    def traits(self) -> dict[str, float]:
        return dict(self._traits)

    def dominant_trait(self) -> str:
        """Return the trait that deviates most from 0.5."""
        return max(self._traits, key=lambda k: abs(self._traits[k] - 0.5))

    def personality_summary(self) -> str:
        """One-line natural language description of current identity."""
        t = self._traits
        parts = []
        if t.get('exploration_style', 0.5) > 0.65:
            parts.append('explorative')
        elif t.get('exploration_style', 0.5) < 0.35:
            parts.append('conservative')
        if t.get('novelty_bias', 0.5) > 0.65:
            parts.append('novelty-seeking')
        elif t.get('novelty_bias', 0.5) < 0.35:
            parts.append('familiarity-preferring')
        if t.get('abstraction_depth', 0.5) > 0.65:
            parts.append('abstract')
        elif t.get('abstraction_depth', 0.5) < 0.35:
            parts.append('concrete')
        if t.get('contradiction_tolerance', 0.5) > 0.65:
            parts.append('contradiction-tolerant')
        return ', '.join(parts) if parts else 'balanced / neutral'

    def snapshot(self) -> dict:
        return {
            'traits':              {k: round(v, 4) for k, v in self._traits.items()},
            'observe_count':       self._observe_count,
            'dominant_trait':      self.dominant_trait(),
            'personality_summary': self.personality_summary(),
        }

    @classmethod
    def get(cls) -> 'IdentityTracker':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
