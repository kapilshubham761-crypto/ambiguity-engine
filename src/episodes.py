"""
Node [Ep] — Episodic Trace Graph
==================================
V3 Step 2: Stores concept sequences as episodes and builds a directed
transition graph — the foundation of proto-reasoning.

Episode schema:
    {
      id:            str   UUID
      ts:            str   ISO timestamp
      concepts:      list[str]   ordered concept texts
      ambiguity:     float  [0, 1]
      active_region: str | None
      outcome:       str   'accepted' | 'rejected'
    }

Transition graph (in-memory + persisted as JSON):
    edges: { "A__SEP__B": { weight: float, count: int } }
    Meaning: concept A was followed by concept B with given frequency.

Transition weight update on each episode:
    weight = weight × transition_decay + 1.0   (count-weighted EMA)

This gives:
    - recent sequences more weight than old ones
    - rarely-used transitions fade toward zero
    - high-frequency paths strengthen over time

Public API:
    record(concepts, ambiguity, region, outcome)  → save episode + update edges
    transitions_from(concept) → dict[str, float]  top transitions
    recent(n) → list[episode dict]
    strongest_paths(n) → list[(A, B, weight)]
    snapshot() → summary dict
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('episodes')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_EP_PATH  = os.path.join(_ROOT, 'data', 'episodes.jsonl')
_TR_PATH  = os.path.join(_ROOT, 'data', 'transitions.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SEP = '__SEP__'

_SINGLETON: Optional['EpisodeStore'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('episodes', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# EpisodeStore                                                                 #
# --------------------------------------------------------------------------- #

class EpisodeStore:
    """
    Persists episodes to a rolling JSONL file.
    Maintains an in-memory + JSON-backed directed transition graph.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._max_episodes     = int(cfg.get('max_episodes', 1000))
        self._min_concepts     = int(cfg.get('min_concepts', 2))
        self._transition_decay = float(cfg.get('transition_decay', 0.95))
        # edges: "A__SEP__B" → {weight, count}
        self._edges: dict[str, dict] = {}
        # In-memory episode buffer — eliminates file reads on every recent() call
        self._buf: deque[dict] = deque(maxlen=self._max_episodes)
        self._record_count = 0   # triggers periodic file trim
        self._load_transitions()
        self._load_episodes()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def record(self, concepts: list[str], ambiguity: float = 0.0,
               region: str | None = None, outcome: str = 'accepted') -> dict | None:
        """
        Save one episode and update transition weights.
        Returns the episode dict, or None if below min_concepts.
        """
        if len(concepts) < self._min_concepts:
            return None

        ep = {
            'id':            str(uuid.uuid4()),
            'ts':            datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
            'concepts':      concepts,
            'ambiguity':     round(float(ambiguity), 4),
            'active_region': region,
            'outcome':       outcome,
        }
        self._append_episode(ep)
        self._update_transitions(concepts)
        return ep

    def transitions_from(self, concept: str,
                         min_weight: float = 0.05) -> dict[str, float]:
        """
        Return {next_concept: weight} for all transitions leaving `concept`.
        Filtered by min_weight and sorted descending.
        """
        prefix  = concept + _SEP
        results = {}
        for key, edge in self._edges.items():
            if key.startswith(prefix):
                nxt = key[len(prefix):]
                w   = float(edge.get('weight', 0.0))
                if w >= min_weight:
                    results[nxt] = round(w, 4)
        return dict(sorted(results.items(), key=lambda x: x[1], reverse=True))

    def recent(self, n: int = 20) -> list[dict]:
        """Return last n episodes from the in-memory buffer."""
        buf = list(self._buf)
        return buf[-n:] if n < len(buf) else buf

    def strongest_paths(self, n: int = 20) -> list[tuple[str, str, float]]:
        """Top-n transition edges by weight."""
        paths = []
        for key, edge in self._edges.items():
            if _SEP in key:
                a, b = key.split(_SEP, 1)
                paths.append((a, b, round(float(edge.get('weight', 0.0)), 4)))
        paths.sort(key=lambda x: x[2], reverse=True)
        return paths[:n]

    def all_concepts_in_transitions(self) -> set[str]:
        """All concept texts that appear in at least one transition."""
        seen: set[str] = set()
        for key in self._edges:
            if _SEP in key:
                a, b = key.split(_SEP, 1)
                seen.add(a)
                seen.add(b)
        return seen

    def cooccurrence_matrix(self) -> dict[str, dict[str, int]]:
        """
        Build an undirected co-occurrence count matrix from all stored episodes.
        Used by Abstractor [Ab] for cluster detection.
        """
        matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ep in self.recent(n=self._max_episodes):
            concepts = ep.get('concepts', [])
            for i, a in enumerate(concepts):
                for b in concepts[i+1:]:
                    if a != b:
                        matrix[a][b] += 1
                        matrix[b][a] += 1
        return {k: dict(v) for k, v in matrix.items()}

    def snapshot(self) -> dict:
        recent = self.recent(5)
        return {
            'total_transitions': len(self._edges),
            'strongest_paths':   self.strongest_paths(10),
            'recent_episodes':   len(recent),
            'last_ts':           recent[-1]['ts'] if recent else None,
        }

    @classmethod
    def get(cls) -> 'EpisodeStore':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _update_transitions(self, concepts: list[str]) -> None:
        """
        For each consecutive pair in the episode, update the transition edge.
        weight = weight × decay + 1.0   (so recent sequences score higher)
        """
        for i in range(len(concepts) - 1):
            a, b = concepts[i], concepts[i + 1]
            if a == b:
                continue
            key  = a + _SEP + b
            edge = self._edges.setdefault(key, {'weight': 0.0, 'count': 0})
            edge['weight'] = round(edge['weight'] * self._transition_decay + 1.0, 4)
            edge['count']  = edge.get('count', 0) + 1
        self._save_transitions()

    def _append_episode(self, ep: dict) -> None:
        self._buf.append(ep)
        self._record_count += 1
        os.makedirs(os.path.dirname(_EP_PATH), exist_ok=True)
        with open(_EP_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(ep, ensure_ascii=False) + '\n')
        # Trim file to max_episodes every 100 records (amortized, not every write)
        if self._record_count % 100 == 0:
            self._trim_episodes()

    def _trim_episodes(self) -> None:
        """Keep only the last max_episodes lines in the JSONL file."""
        try:
            with open(_EP_PATH, encoding='utf-8') as f:
                lines = f.readlines()
            if len(lines) > self._max_episodes:
                with open(_EP_PATH, 'w', encoding='utf-8') as f:
                    f.writelines(lines[-self._max_episodes:])
        except Exception:
            pass

    def _load_episodes(self) -> None:
        """Populate the in-memory buffer from the JSONL file at startup."""
        try:
            with open(_EP_PATH, encoding='utf-8') as f:
                lines = f.readlines()
            for line in lines[-self._max_episodes:]:
                line = line.strip()
                if line:
                    try:
                        self._buf.append(json.loads(line))
                    except Exception:
                        pass
            log.debug('episodes loaded: %d into buffer', len(self._buf))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('episodes load error: %s', e)

    def _save_transitions(self) -> None:
        dir_ = os.path.dirname(_TR_PATH)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps(self._edges, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, _TR_PATH)

    def _load_transitions(self) -> None:
        try:
            with open(_TR_PATH, encoding='utf-8') as f:
                self._edges = json.load(f)
            log.debug('transitions loaded: %d edges', len(self._edges))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('transitions load error: %s', e)
