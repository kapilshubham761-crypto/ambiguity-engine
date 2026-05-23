"""
Node [T] — Tension Tracker
===========================
Tracks concepts that repeatedly produce high ambiguity scores — they become
exploration attractors (directed curiosity).

Core model:
  - Each concept keeps a rolling window of the last `window_size` ambiguity scores.
  - A concept is "hot" when:
      mean(window) >= hot_threshold  AND  len(window) >= hot_min_observations
  - hot(n) returns the top-n hottest concepts (highest mean score, min obs met).
  - Persists to data/tension.json as a flat dict: {concept: [scores...]}

Call sites:
  s5-3  detector.detect_and_log()  — one-line observe hook
  s5-5  homework.generate()        — hot concepts become assignments
  s5-6  attention.bias_sources()   — hot concepts get extra bias bump

Config keys (config.yaml):
  tension.window_size          (default 50)
  tension.hot_threshold        (default 0.60)
  tension.hot_min_observations (default 5)

Implements ITension (protocols.py [0]).
"""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Optional

import yaml

from logger import get_logger
from protocols import ITension

log = get_logger('tension')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'tension.json')

_SINGLETON: Optional['TensionTracker'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('tension', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# TensionTracker                                                               #
# --------------------------------------------------------------------------- #

class TensionTracker:
    """
    Rolling-window ambiguity tension tracker.
    Thread-safe for single-process use (no lock needed — GIL covers deque ops).
    """

    def __init__(self, path: str = _DATA_PATH) -> None:
        self._path = path
        cfg = _cfg()
        self._window_size    = int(cfg.get('window_size', 50))
        self._hot_threshold  = float(cfg.get('hot_threshold', 0.60))
        self._hot_min_obs    = int(cfg.get('hot_min_observations', 5))
        self._windows: dict[str, deque] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(self._path, encoding='utf-8') as f:
                raw: dict = json.load(f)
            for concept, scores in raw.items():
                self._windows[concept] = deque(
                    scores[-self._window_size:], maxlen=self._window_size
                )
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('tension load error: %s', e)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        data = {c: list(w) for c, w in self._windows.items()}
        tmp = self._path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            log.error('tension save error: %s', e)

    # ------------------------------------------------------------------ #
    # ITension interface                                                   #
    # ------------------------------------------------------------------ #

    def observe(self, concept: str, score: float) -> None:
        """Record one ambiguity score observation for a concept."""
        if concept not in self._windows:
            self._windows[concept] = deque(maxlen=self._window_size)
        self._windows[concept].append(float(score))
        self._save()

    def hot(self, n: int = 5) -> list[str]:
        """
        Return up to n concept names with the highest mean ambiguity score,
        filtered to those meeting hot_min_observations.
        """
        qualified = [
            (c, sum(w) / len(w))
            for c, w in self._windows.items()
            if len(w) >= self._hot_min_obs
        ]
        qualified.sort(key=lambda x: x[1], reverse=True)
        return [c for c, mean in qualified if mean >= self._hot_threshold][:n]

    def is_hot(self, concept: str) -> bool:
        """True if concept meets both threshold and min-observation criteria."""
        w = self._windows.get(concept)
        if w is None or len(w) < self._hot_min_obs:
            return False
        return (sum(w) / len(w)) >= self._hot_threshold

    # ------------------------------------------------------------------ #
    # Inspection helpers                                                   #
    # ------------------------------------------------------------------ #

    def mean(self, concept: str) -> float:
        """Mean ambiguity score for a concept, or 0.0 if unseen."""
        w = self._windows.get(concept)
        if not w:
            return 0.0
        return sum(w) / len(w)

    def observations(self, concept: str) -> int:
        """Number of observations recorded for a concept."""
        return len(self._windows.get(concept, []))

    def snapshot(self) -> dict:
        """Return full window state as a plain dict (for UI display)."""
        return {c: list(w) for c, w in self._windows.items()}

    # ------------------------------------------------------------------ #
    # Singleton                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls) -> 'TensionTracker':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
