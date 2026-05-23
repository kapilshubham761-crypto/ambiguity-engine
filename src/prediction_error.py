"""
Node [PE] — Prediction Error Engine
=====================================
V3 Step 12: Measures how wrong the Predictor's forecasts are.

Every time concepts are accepted, we compare:
    predicted_set  (what Predictor said would come next)
    actual_set     (what concepts actually appeared)

Error = 1 - Jaccard(predicted, actual)

When error > surprise_threshold (0.60), the surprising concepts
get a curiosity_boost applied to their working-memory activation.

Keeps a rolling window of errors per concept for variance tracking.

Usage:
    from prediction_error import PredictionError
    pe = PredictionError.get()
    pe.observe(predicted_concepts, actual_concepts)
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

log = get_logger('prediction_error')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'prediction_error.json')

_SINGLETON: Optional['PredictionError'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('prediction_error', {})
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


class PredictionError:
    """
    Rolling prediction-error tracker with curiosity boosting on surprise.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._window             = int(cfg.get('window',             50))
        self._surprise_threshold = float(cfg.get('surprise_threshold', 0.60))
        self._curiosity_boost    = float(cfg.get('curiosity_boost',    0.15))

        # per-concept rolling errors
        self._errors: dict[str, deque] = {}
        # global rolling errors
        self._global_errors: deque = deque(maxlen=self._window)
        self._obs_count = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            for c, errors in raw.get('concept_errors', {}).items():
                self._errors[c] = deque(errors[-self._window:], maxlen=self._window)
            self._global_errors = deque(
                raw.get('global_errors', [])[-self._window:], maxlen=self._window)
            self._obs_count = int(raw.get('obs_count', 0))
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'concept_errors': {c: list(e) for c, e in self._errors.items()},
            'global_errors':  list(self._global_errors),
            'obs_count':      self._obs_count,
            'ts':             time.time(),
        })

    # ------------------------------------------------------------------ #
    # Core                                                                 #
    # ------------------------------------------------------------------ #

    def observe(self, predicted: list[str], actual: list[str]) -> float:
        """
        Compute prediction error and optionally apply curiosity boost.
        Returns the Jaccard error [0..1].
        """
        if not predicted and not actual:
            return 0.0

        p_set = set(predicted)
        a_set = set(actual)
        union = p_set | a_set
        intersection = p_set & a_set
        error = 1.0 - (len(intersection) / len(union)) if union else 0.0

        self._obs_count += 1
        self._global_errors.append(round(error, 4))

        # Track per-concept error for surprising concepts
        surprising = a_set - p_set   # concepts we DIDN'T predict but appeared
        for c in surprising:
            if c not in self._errors:
                self._errors[c] = deque(maxlen=self._window)
            self._errors[c].append(round(error, 4))

        # Apply curiosity boost to surprising concepts when error is high
        if error >= self._surprise_threshold and surprising:
            self._apply_curiosity_boost(list(surprising))

        if self._obs_count % 20 == 0:
            self._save()

        log.debug('prediction_error: error=%.3f surprising=%d', error, len(surprising))
        return error

    def _apply_curiosity_boost(self, concepts: list[str]) -> None:
        try:
            from memory import TemporalMemory
            mem = TemporalMemory.get()
            for c in concepts:
                # Boost by adding a small working memory reinforcement
                mem.reinforce([c], gain_override=self._curiosity_boost)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    def mean_error(self) -> float:
        if not self._global_errors:
            return 0.0
        return round(sum(self._global_errors) / len(self._global_errors), 4)

    def concept_mean_error(self, concept: str) -> float:
        errs = self._errors.get(concept)
        if not errs:
            return 0.0
        return round(sum(errs) / len(errs), 4)

    def most_surprising(self, n: int = 10) -> list[dict]:
        """Concepts with highest mean prediction error."""
        ranked = sorted(
            ((c, sum(e)/len(e)) for c, e in self._errors.items() if e),
            key=lambda x: x[1], reverse=True
        )
        return [{'concept': c, 'mean_error': round(e, 4)} for c, e in ranked[:n]]

    def snapshot(self) -> dict:
        return {
            'obs_count':       self._obs_count,
            'mean_error':      self.mean_error(),
            'most_surprising': self.most_surprising(5),
        }

    @classmethod
    def get(cls) -> 'PredictionError':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
