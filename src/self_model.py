"""
Node [Sm] — Recursive Self-Model
==================================
V3 Step 18: The engine predicts its own future cognitive state.

At each background tick the self-model:
1. Reads current state (entropy, mode, dominant_region)
2. Predicts state at t+horizon using learned drift patterns
3. After horizon ticks, compares prediction vs actual
4. Tracks self-prediction accuracy over time

Persists prediction history to data/self_model.json.

Usage:
    from self_model import SelfModel
    sm = SelfModel.get()
    sm.tick()   # call from background loop
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

log = get_logger('self_model')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'self_model.json')

_SINGLETON: Optional['SelfModel'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('self_model', {})
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


class SelfModel:
    """
    Predicts own future entropy/mode and tracks prediction accuracy.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._horizon        = int(cfg.get('horizon_ticks',  5))
        self._history_window = int(cfg.get('history_window', 20))

        self._tick_count: int = 0
        # Pending predictions: {tick_due: predicted_state}
        self._pending: dict[int, dict] = {}
        # Completed predictions for accuracy tracking
        self._history: deque = deque(maxlen=self._history_window)
        self._accuracy_sum: float = 0.0
        self._accuracy_count: int = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            self._tick_count      = int(raw.get('tick_count', 0))
            self._accuracy_sum    = float(raw.get('accuracy_sum', 0.0))
            self._accuracy_count  = int(raw.get('accuracy_count', 0))
            self._history         = deque(
                raw.get('history', [])[-self._history_window:],
                maxlen=self._history_window)
            # Pending predictions don't survive restart — that's fine
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'tick_count':     self._tick_count,
            'accuracy_sum':   self._accuracy_sum,
            'accuracy_count': self._accuracy_count,
            'history':        list(self._history),
            'ts':             time.time(),
        })

    # ------------------------------------------------------------------ #
    # State reading                                                        #
    # ------------------------------------------------------------------ #

    def _current_state(self) -> dict:
        state = {'entropy': 0.5, 'mode': 'reflective', 'dominant_region': None}
        try:
            from stability import StabilityMonitor
            sm = StabilityMonitor.get()
            state['entropy'] = round(sm.entropy(), 4)
            state['mode']    = sm._current_mode
        except Exception:
            pass
        try:
            from regions import RegionIndex
            ri = RegionIndex.get()
            if ri:
                state['dominant_region'] = ri.active_region()
        except Exception:
            pass
        return state

    # ------------------------------------------------------------------ #
    # Prediction                                                           #
    # ------------------------------------------------------------------ #

    def _predict_from(self, state: dict) -> dict:
        """
        Simple heuristic: entropy tends toward 0.5 (mean-reversion).
        Mode predicted as staying the same unless entropy is extreme.
        """
        e = state.get('entropy', 0.5)
        predicted_entropy = round(e + (0.5 - e) * 0.15, 4)  # 15% mean reversion
        mode = state.get('mode', 'reflective')
        if predicted_entropy < 0.25:
            predicted_mode = 'exploitative'
        elif predicted_entropy > 0.75:
            predicted_mode = 'exploratory'
        else:
            predicted_mode = mode
        return {
            'entropy': predicted_entropy,
            'mode':    predicted_mode,
            'region':  state.get('dominant_region'),
        }

    def _accuracy(self, predicted: dict, actual: dict) -> float:
        """Score prediction accuracy [0..1]."""
        entropy_err = abs(predicted.get('entropy', 0.5) - actual.get('entropy', 0.5))
        mode_match  = 1.0 if predicted.get('mode') == actual.get('mode') else 0.0
        return round((1.0 - entropy_err) * 0.6 + mode_match * 0.4, 4)

    # ------------------------------------------------------------------ #
    # Main tick                                                            #
    # ------------------------------------------------------------------ #

    def tick(self) -> None:
        """Called from background loop each tick."""
        self._tick_count += 1
        current = self._current_state()

        # Check if any pending prediction is due
        due = self._tick_count
        if due in self._pending:
            predicted = self._pending.pop(due)
            acc = self._accuracy(predicted, current)
            self._accuracy_sum   += acc
            self._accuracy_count += 1
            record = {
                'tick': due,
                'predicted': predicted,
                'actual': current,
                'accuracy': acc,
            }
            self._history.append(record)
            log.debug('self_model: prediction acc=%.3f (tick %d)', acc, due)

        # Make a new prediction for horizon ticks from now
        predicted = self._predict_from(current)
        self._pending[self._tick_count + self._horizon] = predicted

        if self._tick_count % 10 == 0:
            self._save()

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    def mean_accuracy(self) -> float:
        if self._accuracy_count == 0:
            return 0.0
        return round(self._accuracy_sum / self._accuracy_count, 4)

    def recent_history(self, n: int = 10) -> list[dict]:
        return list(self._history)[-n:]

    def snapshot(self) -> dict:
        return {
            'tick_count':     self._tick_count,
            'mean_accuracy':  self.mean_accuracy(),
            'accuracy_count': self._accuracy_count,
            'recent':         self.recent_history(3),
        }

    @classmethod
    def get(cls) -> 'SelfModel':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
