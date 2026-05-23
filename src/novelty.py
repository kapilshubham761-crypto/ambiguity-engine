"""
Node [V] — Novelty Tracker
===========================
Phase B: tracks exposure count per concept and scores novelty pressure.
Also detects when the system is looping the same semantic territory and
injects escape concepts (Phase B4 — anti-loop detection).

Novelty score formula:
    novelty_score(concept) = 1 / log(times_seen + e)
    → 1.0 at times_seen=0  (never seen — max novelty)
    → 0.0 asymptotically   (old concepts never fully disappear)

Anti-loop detection (Phase B4):
    Snapshots meta.top(5) on every background tick.
    If the same set (≥80% Jaccard) repeats ≥ loop_min_repeats times:
    inject escape_concepts low-visited concepts into homework.

Implements INovelty (protocols.py [0]).
Persists to data/novelty.json (atomic write).

Call sites:
    B1: detector.detect_and_log() — observe() after every detection
    B2: attention.bias_neighbours() — novelty_score() for candidate ranking
    B3: homework.generate() — novelty_score() for curiosity selection
    B4: teacher background tick — snapshot_top5() + check_loop()
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections import deque
from typing import Optional

import yaml

from logger import get_logger
from protocols import INovelty

log = get_logger('novelty')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_PATH     = os.path.join(_ROOT, 'data', 'novelty.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['NoveltyTracker'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# NoveltyTracker                                                               #
# --------------------------------------------------------------------------- #

class NoveltyTracker:
    """
    Tracks per-concept exposure counts and detects attention loops.

    Internal schema:
        _exposures = {
            concept: {
                "times_seen": int,
                "first_seen": ISO,
                "last_seen":  ISO,
            },
            ...
        }
        _top5_history = deque of frozenset[str]  (Phase B4)
    """

    def __init__(self, path: str = _PATH) -> None:
        self._path      = path
        full_cfg        = _cfg()
        al_cfg          = full_cfg.get('anti_loop', {})
        self._loop_window     = int(al_cfg.get('loop_window',       20))
        self._loop_min_reps   = int(al_cfg.get('loop_min_repeats',   5))
        self._overlap_thresh  = float(al_cfg.get('overlap_threshold', 0.80))
        self._escape_n        = int(al_cfg.get('escape_concepts',    3))
        self._exposures: dict[str, dict] = {}
        self._top5_history: deque = deque(maxlen=self._loop_window)
        self._load()

    # ------------------------------------------------------------------ #
    # INovelty interface                                                   #
    # ------------------------------------------------------------------ #

    def observe(self, concept: str) -> None:
        """Record one exposure for a concept. Called by detector.detect_and_log()."""
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
        if concept in self._exposures:
            self._exposures[concept]['times_seen'] += 1
            self._exposures[concept]['last_seen']   = now
        else:
            self._exposures[concept] = {
                'times_seen': 1,
                'first_seen': now,
                'last_seen':  now,
            }
        self._save()

    def novelty_score(self, concept: str) -> float:
        """
        1 / log(times_seen + e).
        1.0 for unseen concepts; monotonically decreasing thereafter.
        """
        times = self._exposures.get(concept, {}).get('times_seen', 0)
        return 1.0 / math.log(times + math.e)

    def snapshot(self) -> dict:
        """Full state snapshot for UI / debugging."""
        scored = sorted(
            ((c, d['times_seen'], self.novelty_score(c))
             for c, d in self._exposures.items()),
            key=lambda x: x[2], reverse=True
        )
        return {
            'total_concepts':  len(self._exposures),
            'top_novel':       [{'concept': c, 'times_seen': t, 'novelty': round(n, 3)}
                                 for c, t, n in scored[:20]],
            'top5_history_len': len(self._top5_history),
        }

    # ------------------------------------------------------------------ #
    # Phase B4 — Anti-loop detection                                       #
    # ------------------------------------------------------------------ #

    def snapshot_top5(self, meta) -> None:
        """
        Record a snapshot of meta.top(5) texts for loop detection.
        Call this on every background tick (teacher background loop).
        """
        try:
            top = frozenset(t for t, _ in meta.top(5))
            if top:
                self._top5_history.append(top)
            self._save()
        except Exception as e:
            log.debug('snapshot_top5 error: %s', e)

    def check_loop(self) -> bool:
        """Return True if a loop is currently detected."""
        if len(self._top5_history) < self._loop_min_reps:
            return False
        current = self._top5_history[-1] if self._top5_history else frozenset()
        if not current:
            return False
        count = sum(
            1 for snap in self._top5_history
            if self._jaccard(snap, current) >= self._overlap_thresh
        )
        return count >= self._loop_min_reps

    def escape_concepts(self, exclude: set[str] | None = None) -> list[str]:
        """
        Return up to escape_n low-seen concepts not in exclude set.
        These are injected into homework as escape assignments (B4).
        """
        excl = exclude or set()
        candidates = [
            (c, d['times_seen'])
            for c, d in self._exposures.items()
            if c not in excl and d['times_seen'] <= 5
        ]
        candidates.sort(key=lambda x: x[1])
        return [c for c, _ in candidates[:self._escape_n]]

    # ------------------------------------------------------------------ #
    # Convenience                                                          #
    # ------------------------------------------------------------------ #

    def times_seen(self, concept: str) -> int:
        return self._exposures.get(concept, {}).get('times_seen', 0)

    def most_novel(self, n: int = 10, exclude: set[str] | None = None) -> list[str]:
        """Return the n least-seen (highest novelty) known concepts."""
        excl = exclude or set()
        scored = [
            (c, self.novelty_score(c))
            for c in self._exposures
            if c not in excl
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:n]]

    @classmethod
    def get(cls) -> 'NoveltyTracker':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _jaccard(a: frozenset, b: frozenset) -> float:
        if not a and not b:
            return 1.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    def _save(self) -> None:
        dir_ = os.path.dirname(self._path)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps({
            'exposures':    self._exposures,
            'top5_history': [list(s) for s in self._top5_history],
        }, ensure_ascii=False, indent=2)
        tmp_dir = dir_
        with tempfile.NamedTemporaryFile('w', dir=tmp_dir, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, self._path)

    def _load(self) -> None:
        try:
            with open(self._path, encoding='utf-8') as f:
                d = json.load(f)
            self._exposures = d.get('exposures', {})
            for snap in d.get('top5_history', []):
                self._top5_history.append(frozenset(snap))
            log.debug('novelty loaded: %d concepts', len(self._exposures))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('novelty load error: %s', e)
