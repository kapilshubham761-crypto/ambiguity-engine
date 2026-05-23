"""
Node [S] — Stability Monitor
==============================
Phase D: entropy-based cognitive stability + 5-mode meta-modulation.

Phase D2 — Shannon Entropy Monitor
    Measures diversity of activation across the top-N active concepts.
    H = -sum(p_i * log(p_i))  normalised by log(N) → range [0, 1].
    Maintains a rolling entropy window.
    Low entropy → system is over-focused on one cluster.
    High entropy → activations are too diffuse to direct attention.

Phase D3 — Cognitive Mode Engine
    Reads entropy + tension + novelty pressure + region count and
    selects one of 5 modes.  Mode multipliers are read from config.yaml
    (cognitive_mode.mode_weights) and exposed via mode_weights().

    Modes:
        focused      — low entropy + high tension (grinding one cluster)
        exploitative — low entropy + moderate tension (local exploitation)
        exploratory  — high entropy (scattered, needs direction)
        associative  — multi-region or many bridge nodes
        reflective   — otherwise (stable, steady consolidation)

Singleton access via StabilityMonitor.get().
"""

from __future__ import annotations

import math
import os
from collections import deque
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('stability')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['StabilityMonitor'] = None

_DEFAULT_WEIGHTS: dict[str, dict] = {
    'focused':      {'decay': 0.90, 'suppression': 1.10, 'novelty': 0.80},
    'exploratory':  {'decay': 1.05, 'novelty': 1.40,    'curiosity': 1.50},
    'associative':  {'bridge': 1.80, 'suppression': 0.70},
    'exploitative': {'bias': 1.40,   'novelty': 0.60,   'decay': 0.95},
    'reflective':   {'decay': 0.80,  'tension': 1.30},
}


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# StabilityMonitor                                                             #
# --------------------------------------------------------------------------- #

class StabilityMonitor:
    """
    Phase D2 + D3 stability and cognitive mode controller.

    Usage:
        sm = StabilityMonitor.get()
        sm.tick(meta)                   # call each background cycle
        entropy = sm.entropy()          # normalised [0, 1]
        mode    = sm.current_mode(meta) # one of the 5 mode strings
        weights = sm.mode_weights()     # dict from config for current mode
    """

    def __init__(self) -> None:
        cfg       = _cfg()
        stab_cfg  = cfg.get('stability', {})
        mode_cfg  = cfg.get('cognitive_mode', {})
        self._entropy_window = int(stab_cfg.get('entropy_window', 10))
        self._entropy_min    = float(stab_cfg.get('entropy_min', 0.20))
        self._entropy_max    = float(stab_cfg.get('entropy_max', 0.80))
        self._mode_enabled   = bool(mode_cfg.get('enabled', True))
        self._mode_weights   = mode_cfg.get('mode_weights', _DEFAULT_WEIGHTS)

        self._entropy_history: deque[float] = deque(maxlen=self._entropy_window)
        self._current_mode: str = 'reflective'

    # ------------------------------------------------------------------ #
    # Phase D2 — entropy computation                                       #
    # ------------------------------------------------------------------ #

    def tick(self, meta) -> float:
        """
        Compute current entropy from meta.top() activations.
        Records in rolling window.  Returns the raw entropy value.
        """
        try:
            top = meta.top(20)           # [(text, activation), ...]
        except Exception:
            return 0.0

        activations = [float(a) for _, a in top if a > 0]
        h = self._shannon(activations)
        self._entropy_history.append(h)
        log.debug('stability tick: entropy=%.3f  mode=%s', h, self._current_mode)
        return h

    def entropy(self) -> float:
        """Latest normalised entropy value (0 = no history yet)."""
        if not self._entropy_history:
            return 0.0
        return self._entropy_history[-1]

    def mean_entropy(self) -> float:
        """Rolling mean entropy over the window."""
        if not self._entropy_history:
            return 0.0
        return sum(self._entropy_history) / len(self._entropy_history)

    def is_overloaded(self) -> bool:
        """True when entropy < entropy_min (too focused)."""
        return bool(self._entropy_history) and self.entropy() < self._entropy_min

    def is_diffuse(self) -> bool:
        """True when entropy > entropy_max (too scattered)."""
        return bool(self._entropy_history) and self.entropy() > self._entropy_max

    def is_stable(self) -> bool:
        """True when entropy is within the healthy band."""
        return not self.is_overloaded() and not self.is_diffuse()

    # ------------------------------------------------------------------ #
    # Phase D3 — cognitive mode selection                                  #
    # ------------------------------------------------------------------ #

    def current_mode(self, meta=None, regions=None) -> str:
        """
        Determine and cache the current cognitive mode.

        Priority order (first match wins):
            1. exploratory  — entropy too high (diffuse)
            2. associative  — multiple active graph regions
            3. focused      — entropy too low + tension high
            4. exploitative — entropy low, tension moderate
            5. reflective   — default (stable state)
        """
        if not self._mode_enabled:
            self._current_mode = 'reflective'
            return self._current_mode

        h = self.entropy()
        tension_mean = self._mean_tension()
        region_count = 0
        if regions is not None:
            try:
                region_count = regions.region_count()
            except Exception:
                pass

        if h > self._entropy_max:
            mode = 'exploratory'
        elif region_count >= 3:
            mode = 'associative'
        elif h < self._entropy_min and tension_mean > 0.5:
            mode = 'focused'
        elif h < self._entropy_min:
            mode = 'exploitative'
        else:
            mode = 'reflective'

        if mode != self._current_mode:
            log.debug('cognitive mode: %s → %s  (h=%.2f tension=%.2f regions=%d)',
                      self._current_mode, mode, h, tension_mean, region_count)
        self._current_mode = mode
        return mode

    def mode_weights(self) -> dict:
        """Return config multipliers for the current mode."""
        return dict(self._mode_weights.get(self._current_mode, {}))

    # ------------------------------------------------------------------ #
    # Snapshot                                                             #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        return {
            'entropy':       round(self.entropy(), 4),
            'mean_entropy':  round(self.mean_entropy(), 4),
            'entropy_min':   self._entropy_min,
            'entropy_max':   self._entropy_max,
            'is_overloaded': self.is_overloaded(),
            'is_diffuse':    self.is_diffuse(),
            'is_stable':     self.is_stable(),
            'mode':          self._current_mode,
            'mode_weights':  self.mode_weights(),
            'history_len':   len(self._entropy_history),
        }

    # ------------------------------------------------------------------ #
    # Singleton                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls) -> 'StabilityMonitor':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _shannon(activations: list[float]) -> float:
        """
        Normalised Shannon entropy of a probability distribution.
        Returns 0 if fewer than 2 non-zero values.
        """
        if len(activations) < 2:
            return 0.0
        total = sum(activations)
        if total == 0:
            return 0.0
        probs  = [a / total for a in activations]
        raw_h  = -sum(p * math.log(p) for p in probs if p > 0)
        max_h  = math.log(len(probs))
        return round(raw_h / max_h, 4) if max_h > 0 else 0.0

    @staticmethod
    def _mean_tension() -> float:
        """Read mean tension level from TensionTracker singleton."""
        try:
            from tension import TensionTracker
            t = TensionTracker.get()
            hot = t.hot(n=10)
            if not hot:
                return 0.0
            return sum(t.mean(c) for c in hot) / len(hot)
        except Exception:
            return 0.0
