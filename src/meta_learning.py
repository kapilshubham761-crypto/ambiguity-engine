"""
Node [ML] — Meta-Learning
===========================
V3 Step 20: Tracks which learning strategies improve prediction accuracy.

Strategies evaluated:
    high_novelty_strength  — exploration favored
    high_bias_strength     — focused/exploitative
    fast_decay             — fresh memories win
    slow_decay             — older memories persist
    aggressive_fatigue     — repetition strongly penalised

Each strategy is scored by comparing prediction error means before/after
the strategy was effectively active (inferred from Evolver params).

Usage:
    from meta_learning import MetaLearner
    ml = MetaLearner.get()
    ml.tick()   # call from background loop
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

log = get_logger('meta_learning')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'meta_learning.json')

_SINGLETON: Optional['MetaLearner'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('meta_learning', {})
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


class MetaLearner:
    """
    Scores learning strategies by their association with prediction accuracy.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._strategy_window       = int(cfg.get('strategy_window',    30))
        self._effectiveness_decay   = float(cfg.get('effectiveness_decay', 0.95))
        self._strategies            = list(cfg.get('strategies', [
            'high_novelty_strength', 'high_bias_strength',
            'fast_decay', 'slow_decay', 'aggressive_fatigue',
        ]))

        # Per-strategy rolling effectiveness scores
        self._scores: dict[str, float] = {s: 0.5 for s in self._strategies}
        # Rolling observation windows per strategy
        self._windows: dict[str, deque] = {
            s: deque(maxlen=self._strategy_window) for s in self._strategies
        }
        self._tick_count: int = 0
        self._best_strategy: str = self._strategies[0] if self._strategies else 'none'
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            for s in self._strategies:
                if s in raw.get('scores', {}):
                    self._scores[s] = float(raw['scores'][s])
                if s in raw.get('windows', {}):
                    self._windows[s] = deque(
                        raw['windows'][s][-self._strategy_window:],
                        maxlen=self._strategy_window)
            self._tick_count    = int(raw.get('tick_count', 0))
            self._best_strategy = raw.get('best_strategy', self._best_strategy)
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'scores':        {k: round(v, 4) for k, v in self._scores.items()},
            'windows':       {k: list(v) for k, v in self._windows.items()},
            'tick_count':    self._tick_count,
            'best_strategy': self._best_strategy,
            'ts':            time.time(),
        })

    # ------------------------------------------------------------------ #
    # Active strategy detection                                            #
    # ------------------------------------------------------------------ #

    def _active_strategies(self) -> list[str]:
        """Infer which strategies are currently active from Evolver params."""
        active = []
        try:
            from evolver import Evolver
            params = Evolver.get().current_params()
            nov = params.get('novelty_strength', 0.0)
            bias = params.get('bias_strength', 0.0)
            if nov > 0.5:
                active.append('high_novelty_strength')
            if bias > 0.5:
                active.append('high_bias_strength')
        except Exception:
            pass
        try:
            import yaml as _yaml
            with open(_CFG_PATH, encoding='utf-8') as f:
                cfg = _yaml.safe_load(f)
            meta_cfg = cfg.get('meta_state', {})
            fatigue_k = float(meta_cfg.get('fatigue_k', 0.0))
            decay_rate = float(meta_cfg.get('decay_rate', 0.97))
            if fatigue_k > 0.15:
                active.append('aggressive_fatigue')
            if decay_rate < 0.95:
                active.append('fast_decay')
            elif decay_rate > 0.985:
                active.append('slow_decay')
        except Exception:
            pass
        return active

    # ------------------------------------------------------------------ #
    # Main tick                                                            #
    # ------------------------------------------------------------------ #

    def tick(self) -> None:
        """Update strategy scores based on current prediction error."""
        self._tick_count += 1

        # Get current prediction accuracy (lower error = better)
        error = 0.5
        try:
            from prediction_error import PredictionError
            error = PredictionError.get().mean_error()
        except Exception:
            pass

        accuracy = 1.0 - error  # higher = better

        # Decay all scores
        for s in self._strategies:
            self._scores[s] *= self._effectiveness_decay

        # Credit active strategies
        active = self._active_strategies()
        for s in active:
            if s in self._windows:
                self._windows[s].append(accuracy)
                # Score = EMA of accuracy in window
                window = list(self._windows[s])
                if window:
                    mean_acc = sum(window) / len(window)
                    self._scores[s] = max(self._scores[s], mean_acc)

        if self._scores:
            self._best_strategy = max(self._scores, key=lambda s: self._scores[s])

        if self._tick_count % 10 == 0:
            self._save()
            log.debug('meta_learning: best=%s scores=%s',
                      self._best_strategy,
                      {k: round(v, 3) for k, v in self._scores.items()})

    def strategy_scores(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in self._scores.items()}

    def best_strategy(self) -> str:
        return self._best_strategy

    def snapshot(self) -> dict:
        return {
            'tick_count':     self._tick_count,
            'strategy_scores': self.strategy_scores(),
            'best_strategy':  self._best_strategy,
        }

    @classmethod
    def get(cls) -> 'MetaLearner':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
