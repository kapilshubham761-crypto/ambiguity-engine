"""
Node [Bn] — Cross-Modal Binding
=================================
V3 Step 15: Detects concepts that act as semantic bridges across
multiple distinct contexts (episode domains).

A concept is a bridge if it appears in >= min_domain_spread distinct
episode contexts.  Bridge strength = distinct context count / total
episodes (normalised).

These cross-modal bridges are rich integration points — they tie
otherwise disconnected knowledge clusters together.

Usage:
    from binding import BindingDetector
    bd = BindingDetector.get()
    bd.update()               # recompute from episode history
    bridges = bd.top_bridges()
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('binding')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'bindings.json')

_SINGLETON: Optional['BindingDetector'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('binding', {})
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


class BindingDetector:
    """
    Identifies concepts that bridge multiple distinct episode regions/contexts.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._min_domain_spread = int(cfg.get('min_domain_spread', 2))
        self._bridge_decay      = float(cfg.get('bridge_decay',    0.97))
        self._max_bridges       = int(cfg.get('max_bridges',       200))

        # concept → {region_id: count}
        self._concept_regions: dict[str, dict[str, int]] = {}
        # concept → bridge strength score
        self._bridge_scores: dict[str, float] = {}
        self._update_count: int = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            self._concept_regions = raw.get('concept_regions', {})
            self._bridge_scores   = raw.get('bridge_scores', {})
            self._update_count    = int(raw.get('update_count', 0))
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'concept_regions': self._concept_regions,
            'bridge_scores':   self._bridge_scores,
            'update_count':    self._update_count,
            'ts':              time.time(),
        })

    # ------------------------------------------------------------------ #
    # Core                                                                 #
    # ------------------------------------------------------------------ #

    def observe_episode(self, concepts: list[str], region: str) -> None:
        """Record which region an episode's concepts appeared in."""
        if not region:
            return
        for c in concepts:
            if c not in self._concept_regions:
                self._concept_regions[c] = {}
            self._concept_regions[c][region] = (
                self._concept_regions[c].get(region, 0) + 1)

    def update(self) -> int:
        """
        Recompute bridge scores from episode history.
        Returns count of bridges detected.
        """
        self._update_count += 1

        # Decay existing scores
        for c in list(self._bridge_scores):
            self._bridge_scores[c] *= self._bridge_decay
            if self._bridge_scores[c] < 0.01:
                del self._bridge_scores[c]

        # Also scan episode store directly
        try:
            from episodes import EpisodeStore
            ep = EpisodeStore.get()
            for episode in ep.recent(500):
                region = episode.get('region', '')
                concepts = episode.get('concepts', [])
                if region and concepts:
                    self.observe_episode(concepts, region)
        except Exception:
            pass

        # Compute bridge strength
        new_bridges = {}
        for c, region_map in self._concept_regions.items():
            domain_spread = len(region_map)
            if domain_spread >= self._min_domain_spread:
                total_appearances = sum(region_map.values())
                strength = min(1.0, domain_spread / max(total_appearances ** 0.5, 1))
                new_bridges[c] = round(strength, 4)

        # Merge with decayed scores
        for c, s in new_bridges.items():
            self._bridge_scores[c] = max(self._bridge_scores.get(c, 0.0), s)

        # Prune to max_bridges
        if len(self._bridge_scores) > self._max_bridges:
            sorted_items = sorted(self._bridge_scores.items(), key=lambda x: x[1])
            for c, _ in sorted_items[:len(self._bridge_scores) - self._max_bridges]:
                del self._bridge_scores[c]

        self._save()
        count = len(self._bridge_scores)
        log.debug('binding: %d bridges detected', count)
        return count

    def bridge_strength(self, concept: str) -> float:
        return self._bridge_scores.get(concept, 0.0)

    def is_bridge(self, concept: str) -> bool:
        return concept in self._bridge_scores

    def top_bridges(self, n: int = 20) -> list[dict]:
        sorted_items = sorted(
            self._bridge_scores.items(), key=lambda x: x[1], reverse=True)
        return [
            {
                'concept': c,
                'strength': s,
                'domain_spread': len(self._concept_regions.get(c, {})),
            }
            for c, s in sorted_items[:n]
        ]

    def snapshot(self) -> dict:
        return {
            'bridge_count':  len(self._bridge_scores),
            'update_count':  self._update_count,
            'top_bridges':   self.top_bridges(5),
        }

    @classmethod
    def get(cls) -> 'BindingDetector':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
