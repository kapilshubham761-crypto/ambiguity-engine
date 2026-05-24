"""
Node [Ev] — Architecture Evolver
==================================
V3 Step 11: Observes engine metrics and nudges config parameters
to improve performance over time.  No gradient descent — pure
heuristic hill-climbing on a rolling metric window.

Metrics tracked:
    novelty_balance      (from ReflectionMonitor)
    entropy              (from StabilityMonitor)
    repetition_rate      (from ReflectionMonitor)
    ambiguity_load       (from ReflectionMonitor)
    semantic_depth       (from TemporalMemory)

Adaptation rules:
    entropy too low  → bump novelty_strength (+delta)
    entropy too high → bump bias_strength (+delta)
    repetition high  → bump novelty_strength (+delta)
    ambiguity load high → bump attention.bias_strength (+delta)

All deltas clamped to max_delta=0.05 per cycle.
Evolved params written to data/evolved_params.json.

Usage:
    from evolver import Evolver
    ev = Evolver.get()
    ev.tick()   # call from background loop
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from collections import deque
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('evolver')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'evolved_params.json')

_SINGLETON: Optional['Evolver'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('evolver', {})
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


class Evolver:
    """
    Watches rolling metrics and adapts attention/novelty/bias strengths.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._observe_window = int(cfg.get('observe_window', 20))
        self._adapt_every    = int(cfg.get('adapt_every',    30))
        self._max_delta      = float(cfg.get('max_delta',     0.05))

        self._tick_count: int = 0
        self._history: deque = deque(maxlen=self._observe_window)

        # Evolved parameters (start from config defaults)
        self._params: dict = self._load_or_default()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load_or_default(self) -> dict:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                data = json.load(f)
            return data.get('params', self._default_params())
        except Exception:
            return self._default_params()

    def _default_params(self) -> dict:
        try:
            with open(_CFG_PATH, encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            att = cfg.get('attention', {})
            return {
                'novelty_strength': float(att.get('novelty_strength', 0.0)),
                'bias_strength':    float(att.get('bias_strength',    0.0)),
            }
        except Exception:
            return {'novelty_strength': 0.0, 'bias_strength': 0.0}

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'params':     self._params,
            'tick_count': self._tick_count,
            'ts':         time.time(),
        })

    # ------------------------------------------------------------------ #
    # Metric collection                                                    #
    # ------------------------------------------------------------------ #

    def _collect_metrics(self) -> dict | None:
        metrics = {}
        try:
            from reflection import ReflectionMonitor
            rf = ReflectionMonitor.get()
            report = rf.report()
            metrics['novelty_balance']  = float(report.get('novelty_balance',  0.5))
            metrics['entropy']          = float(report.get('entropy',          0.5))
            metrics['repetition_rate']  = float(report.get('repetition_rate',  0.0))
            metrics['ambiguity_load']   = float(report.get('ambiguity_load',   0.0))
            metrics['semantic_depth']   = float(report.get('semantic_depth',   0.0))
        except Exception:
            return None
        return metrics

    # ------------------------------------------------------------------ #
    # Adaptation                                                           #
    # ------------------------------------------------------------------ #

    def _adapt(self) -> None:
        if not self._history:
            return

        window = list(self._history)
        avg = {k: sum(m[k] for m in window) / len(window) for k in window[0]}

        delta = self._max_delta
        changes = []

        # Low entropy → system is stuck → push novelty
        if avg['entropy'] < 0.25:
            self._params['novelty_strength'] = min(
                1.0, self._params['novelty_strength'] + delta)
            changes.append(f'entropy={avg["entropy"]:.2f} low → +novelty_strength')

        # High entropy → system is drifting → anchor with bias
        elif avg['entropy'] > 0.75:
            self._params['bias_strength'] = min(
                1.0, self._params['bias_strength'] + delta)
            changes.append(f'entropy={avg["entropy"]:.2f} high → +bias_strength')

        # High repetition → loop detected → push novelty
        if avg['repetition_rate'] > 0.65:
            self._params['novelty_strength'] = min(
                1.0, self._params['novelty_strength'] + delta)
            changes.append(f'repetition={avg["repetition_rate"]:.2f} → +novelty_strength')

        # High ambiguity load → pull bias inward
        if avg['ambiguity_load'] > 0.70:
            self._params['bias_strength'] = min(
                1.0, self._params['bias_strength'] + delta)
            changes.append(f'ambiguity={avg["ambiguity_load"]:.2f} → +bias_strength')

        if changes:
            self._save()
            log.info('evolver: adapted — %s', '; '.join(changes))
        else:
            log.debug('evolver: no adaptation needed (tick %d)', self._tick_count)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def _live_int(self, key: str, fallback: int) -> int:
        try:
            from config import Config
            v = Config.get_instance().get('evolver', key)
            return int(v) if v is not None else fallback
        except Exception:
            return fallback

    def _live_float(self, key: str, fallback: float) -> float:
        try:
            from config import Config
            v = Config.get_instance().get('evolver', key)
            return float(v) if v is not None else fallback
        except Exception:
            return fallback

    def tick(self) -> None:
        """Called from the background loop."""
        self._tick_count += 1
        m = self._collect_metrics()
        if m:
            self._history.append(m)

        adapt_every = self._live_int('adapt_every', self._adapt_every)
        if self._tick_count % adapt_every == 0:
            self._max_delta = self._live_float('max_delta', self._max_delta)
            self._adapt()

    def current_params(self) -> dict:
        return dict(self._params)

    def history(self) -> list[dict]:
        return list(self._history)

    def snapshot(self) -> dict:
        return {
            'tick_count':  self._tick_count,
            'params':      self._params,
            'history_len': len(self._history),
            'latest':      self._history[-1] if self._history else {},
        }

    @classmethod
    def get(cls) -> 'Evolver':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
