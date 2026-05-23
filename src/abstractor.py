"""
Node [Ab] — Concept Abstractor
==================================
V3 Step 7: Automatically generates higher-order concepts from recurring
co-occurrence clusters in episodic memory.

This is where analogies and generalized reasoning emerge.

Mechanism:
    1. Read co-occurrence matrix from EpisodeStore [Ep]
    2. Find clusters of concepts that repeatedly appear together
       (co-occurrence count ≥ min_cooccurrence, cluster size ≥ min_cluster_size)
    3. For each qualifying cluster:
       - Compute an emergence_score = mean co-occurrence density
       - Create an AbstractConcept entry if score ≥ emergence_threshold
    4. Name the abstract concept = most central member (highest degree in cluster)
       prefixed with "~" to mark it as synthetic

AbstractConcept schema:
    {
      id:               str   UUID
      name:             str   "~" + central member
      members:          list[str]
      emergence_score:  float   mean co-occurrence density
      stability:        float   fraction of members still in semantic memory
      reuse_frequency:  int     times this cluster has been observed
      first_seen:       str     ISO
      last_updated:     str     ISO
    }

Abstraction lifecycle:
    - New:    cluster first detected
    - Stable: seen multiple times; stability > 0.7
    - Decay:  members drop from semantic memory → stability falls → cluster dissolves

Called at check_in time (every 3h) — not every accept.

Public API:
    run(graph) → list[AbstractConcept dicts]   detect + update abstractions
    all() → list[dict]                          current abstract concepts
    snapshot() → dict
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('abstractor')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_PATH     = os.path.join(_ROOT, 'data', 'abstractions.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['Abstractor'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('abstractor', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Abstractor                                                                   #
# --------------------------------------------------------------------------- #

class Abstractor:
    """
    Detects and maintains higher-order abstract concept nodes from co-occurrence
    patterns in episodic memory.
    """

    def __init__(self, path: str = _PATH) -> None:
        cfg = _cfg()
        self._min_cooccurrence  = int(cfg.get('min_cooccurrence', 5))
        self._min_cluster_size  = int(cfg.get('min_cluster_size', 3))
        self._emergence_thresh  = float(cfg.get('emergence_threshold', 0.60))
        self._path = path
        self._abstractions: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(self) -> list[dict]:
        """
        Detect abstract concepts from current episodic co-occurrence patterns.
        Merges with existing abstractions (updates stability + reuse_frequency).
        Returns list of all current abstract concepts.
        """
        matrix = self._cooccurrence_matrix()
        clusters = self._find_clusters(matrix)

        now = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
        updated = 0

        for cluster in clusters:
            score = self._emergence_score(cluster, matrix)
            if score < self._emergence_thresh:
                continue

            central = self._central_member(cluster, matrix)
            name    = '~' + central
            stab    = self._stability(cluster)

            existing = self._find_existing(cluster)
            if existing:
                existing['stability']       = round(stab, 4)
                existing['emergence_score'] = round(score, 4)
                existing['reuse_frequency'] = existing.get('reuse_frequency', 0) + 1
                existing['last_updated']    = now
                existing['members']         = sorted(cluster)
                updated += 1
            else:
                self._abstractions.append({
                    'id':               str(uuid.uuid4()),
                    'name':             name,
                    'members':          sorted(cluster),
                    'emergence_score':  round(score, 4),
                    'stability':        round(stab, 4),
                    'reuse_frequency':  1,
                    'first_seen':       now,
                    'last_updated':     now,
                })

        if clusters:
            self._save()
            log.debug('abstractor: %d clusters → %d abstractions (%d updated)',
                      len(clusters), len(self._abstractions), updated)
        return list(self._abstractions)

    def all(self) -> list[dict]:
        return list(self._abstractions)

    def stable(self, min_stability: float = 0.7) -> list[dict]:
        return [a for a in self._abstractions if a.get('stability', 0.0) >= min_stability]

    def snapshot(self) -> dict:
        return {
            'total':       len(self._abstractions),
            'stable':      len(self.stable()),
            'top':         sorted(self._abstractions,
                                  key=lambda a: a.get('emergence_score', 0),
                                  reverse=True)[:10],
        }

    @classmethod
    def get(cls) -> 'Abstractor':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Cluster detection                                                    #
    # ------------------------------------------------------------------ #

    def _cooccurrence_matrix(self) -> dict[str, dict[str, int]]:
        try:
            from episodes import EpisodeStore
            return EpisodeStore.get().cooccurrence_matrix()
        except Exception:
            return {}

    def _find_clusters(self, matrix: dict[str, dict[str, int]]) -> list[set[str]]:
        """
        Simple greedy cluster detection:
        For each concept, find its co-occurring neighbours above min_cooccurrence.
        Merge overlapping neighbour sets into clusters.
        """
        adjacency: dict[str, set[str]] = defaultdict(set)
        for a, neighbours in matrix.items():
            for b, count in neighbours.items():
                if count >= self._min_cooccurrence:
                    adjacency[a].add(b)
                    adjacency[b].add(a)

        # Union-find to merge connected components
        parent = {n: n for n in adjacency}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for a, neighbours in adjacency.items():
            for b in neighbours:
                union(a, b)

        groups: dict[str, set] = defaultdict(set)
        for node in adjacency:
            groups[find(node)].add(node)

        return [g for g in groups.values() if len(g) >= self._min_cluster_size]

    @staticmethod
    def _emergence_score(cluster: set[str],
                         matrix: dict[str, dict[str, int]]) -> float:
        """
        Mean co-occurrence density within the cluster.
        = sum of internal edges / maximum possible edges
        """
        members = list(cluster)
        n = len(members)
        if n < 2:
            return 0.0
        max_edges = n * (n - 1) / 2
        total = 0
        for i in range(n):
            for j in range(i + 1, n):
                total += matrix.get(members[i], {}).get(members[j], 0)
        return round(total / (max_edges * 10), 4)    # normalised by 10× min_cooccurrence

    @staticmethod
    def _central_member(cluster: set[str],
                        matrix: dict[str, dict[str, int]]) -> str:
        """Member with highest total co-occurrence weight within the cluster."""
        best, best_score = '', 0
        for m in cluster:
            score = sum(matrix.get(m, {}).get(other, 0) for other in cluster if other != m)
            if score > best_score:
                best_score, best = score, m
        return best or next(iter(cluster))

    @staticmethod
    def _stability(cluster: set[str]) -> float:
        """Fraction of cluster members currently in semantic memory."""
        try:
            from memory import TemporalMemory
            mem = TemporalMemory.get()
            in_sem = sum(1 for c in cluster if mem.in_semantic(c))
            return in_sem / len(cluster)
        except Exception:
            return 0.0

    def _find_existing(self, cluster: set[str]) -> dict | None:
        """Find an abstraction that shares ≥ 50% of members with this cluster."""
        for a in self._abstractions:
            existing_set = set(a.get('members', []))
            if len(cluster & existing_set) / max(len(cluster | existing_set), 1) >= 0.50:
                return a
        return None

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        dir_ = os.path.dirname(self._path)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps(self._abstractions, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, self._path)

    def _load(self) -> None:
        try:
            with open(self._path, encoding='utf-8') as f:
                self._abstractions = json.load(f)
            log.debug('abstractions loaded: %d', len(self._abstractions))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('abstractor load error: %s', e)
