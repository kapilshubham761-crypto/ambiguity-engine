"""
Node [G] — Detector
====================
Ambiguity detection: variance + cluster + bridge.

Three metrics combined into a single normalised score in [0, 1]:
  - Variance  : mean pairwise cosine *distance* of input concept embeddings
  - Cluster   : k=2 centroid separation (how bimodal is the concept cloud?)
  - Bridge    : do input concepts pull toward disconnected graph regions?

Each metric is independently normalised then blended with tunable weights.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

import numpy as np
from sklearn.cluster import KMeans

from logger import get_logger

log = get_logger('detector')

# --------------------------------------------------------------------------- #
# Config defaults (overridden by the SemanticGraph instance if passed)         #
# --------------------------------------------------------------------------- #

def _load_weights():
    try:
        import yaml, os
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), '..', 'config.yaml')))
        return cfg['ambiguity']['weights']
    except Exception:
        return {'variance': 0.45, 'cluster': 0.45, 'bridge': 0.10}

_WEIGHTS  = _load_weights()
_LOW_MAX  = 0.3
_HIGH_MIN = 0.6

_LOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'logs', 'ambiguity_scores.jsonl')


# --------------------------------------------------------------------------- #
# Result dataclass                                                             #
# --------------------------------------------------------------------------- #

@dataclass
class AmbiguityResult:
    score:          float           # combined [0, 1]
    level:          str             # 'low' | 'medium' | 'high'
    variance:       float           # raw variance metric [0, 1]
    cluster:        float           # raw cluster metric [0, 1]
    bridge:         float           # raw bridge metric [0, 1]
    concept_count:  int
    timestamp:      str


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / denom)


def _variance_metric(embeddings: np.ndarray) -> float:
    """Mean pairwise cosine distance — vectorized with one matrix multiply."""
    n = len(embeddings)
    if n < 2:
        return 0.0
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = embeddings / norms
    sim_mat = normed @ normed.T       # (n, n) all pairwise cosines
    i, j = np.triu_indices(n, k=1)
    mean_dist = float(np.mean(1.0 - sim_mat[i, j]))
    return min(mean_dist, 1.0)


def _cluster_metric(embeddings: np.ndarray) -> float:
    """
    4.1b — k=2 KMeans: cosine distance between the two centroids.
    If there are fewer than 2 concepts, returns 0.
    Normalised to [0, 1] via the same cosine distance cap.
    """
    n = len(embeddings)
    if n < 2:
        return 0.0
    k = min(2, n)
    km = KMeans(n_clusters=k, n_init=3, random_state=42)
    km.fit(embeddings)
    if k == 1:
        return 0.0
    c0, c1 = km.cluster_centers_[0], km.cluster_centers_[1]
    return min(_cosine_distance(c0, c1), 1.0)


def _bridge_metric(embeddings: np.ndarray, graph) -> float:
    """
    Bridge score — uses graph's pre-built normalised embedding matrix
    to avoid rebuilding a (N, 384) array on every call.
    """
    if graph is None or graph.node_count == 0:
        return 0.0
    mat = getattr(graph, '_emb_matrix', None)
    ids = getattr(graph, '_emb_node_ids', None)
    if mat is None or ids is None or len(ids) == 0:
        return 0.0

    n = len(embeddings)
    if n < 2:
        return 0.0

    centroids: list[np.ndarray] = []
    for emb in embeddings:
        v = np.array(emb, dtype=np.float32)
        norm = np.linalg.norm(v)
        if norm == 0:
            continue
        v = v / norm
        sims = mat @ v                          # (N,) — one BLAS call, matrix pre-normalised
        closest_id = ids[int(np.argmax(sims))]

        neighbours = list(graph._g.neighbors(closest_id))
        if not neighbours:
            continue
        nb_embs = np.array(
            [graph._g.nodes[nb]['embedding'] for nb in neighbours
             if graph._g.nodes[nb].get('embedding')],
            dtype=np.float32,
        )
        if len(nb_embs) == 0:
            continue
        centroids.append(nb_embs.mean(axis=0))

    if len(centroids) < 2:
        return 0.0

    C = np.array(centroids, dtype=np.float32)
    norms = np.linalg.norm(C, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    C = C / norms
    sim_mat = C @ C.T
    i, j = np.triu_indices(len(centroids), k=1)
    return min(float(np.mean(1.0 - sim_mat[i, j])), 1.0)


def _level(score: float) -> str:
    if score < _LOW_MAX:
        return 'low'
    if score < _HIGH_MIN:
        return 'medium'
    return 'high'


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def detect(concepts: list, graph=None,
           weights: Optional[dict] = None) -> AmbiguityResult:
    """
    Compute the ambiguity score for a list of Concept namedtuples.

    Parameters
    ----------
    concepts : list[Concept]   output of extractor.extract()
    graph    : SemanticGraph   optional; enables bridge metric
    weights  : dict            override {'variance', 'cluster', 'bridge'} blend
    """
    w = weights or _WEIGHTS

    if not concepts:
        result = AmbiguityResult(
            score=0.0, level='low',
            variance=0.0, cluster=0.0, bridge=0.0,
            concept_count=0,
            timestamp=datetime.utcnow().isoformat(timespec='seconds'),
        )
        _log_score(result, input_text='<empty>')
        return result

    embs = np.array([c.embedding for c in concepts], dtype=np.float32)

    var_score  = _variance_metric(embs)
    clus_score = _cluster_metric(embs)
    brdg_score = _bridge_metric(embs, graph)

    combined = (
        w['variance'] * var_score +
        w['cluster']  * clus_score +
        w['bridge']   * brdg_score
    )
    combined = float(np.clip(combined, 0.0, 1.0))

    # With only 2 concepts the variance signal is unreliable (one pair, no triangulation).
    # Cap at medium so a simple factual question with 2 dissimilar words can't score high.
    if len(concepts) < 3:
        combined = min(combined, _HIGH_MIN - 0.01)

    result = AmbiguityResult(
        score         = round(combined, 4),
        level         = _level(combined),
        variance      = round(var_score,  4),
        cluster       = round(clus_score, 4),
        bridge        = round(brdg_score, 4),
        concept_count = len(concepts),
        timestamp     = datetime.utcnow().isoformat(timespec='seconds'),
    )
    log.debug(
        'Ambiguity score=%.3f (%s)  var=%.3f  clus=%.3f  brdg=%.3f  n=%d',
        result.score, result.level,
        result.variance, result.cluster, result.bridge,
        result.concept_count,
    )
    return result


def detect_and_log(input_text: str, concepts: list,
                   graph=None, weights: Optional[dict] = None) -> AmbiguityResult:
    """4.3 — detect() then append a JSONL entry to the score log."""
    result = detect(concepts, graph=graph, weights=weights)
    _log_score(result, input_text=input_text)
    # s5-3 — feed each concept's score into the tension tracker
    try:
        from tension import TensionTracker
        _t = TensionTracker.get()
        for c in concepts:
            _t.observe(c.text, result.score)
    except Exception:
        pass
    # B1 — record concept exposure in novelty tracker
    try:
        from novelty import NoveltyTracker
        _n = NoveltyTracker.get()
        for c in concepts:
            _n.observe(c.text)
    except Exception:
        pass
    return result


def _log_score(result: AmbiguityResult, input_text: str) -> None:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    entry = asdict(result)
    entry['input_text'] = input_text[:200]
    with open(_LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
