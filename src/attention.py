"""
Node [N] — Attention Bias
==========================
Reranks candidates using current cognitive pressure from meta-state [M].

Core formula (pure, no side effects):
    biased_score = base_score × (1 + strength × activation)
    then normalised so max = 1.0

At strength = 0.0  →  pure no-op; original order is preserved exactly.
At strength = 1.0  →  a concept at full activation (1.0) gets 2× its score.
At strength = 2.0  →  full activation = 3× score (use carefully).

Three named call sites (thin wrappers over the core):
    bias_neighbours  →  modulator._get_neighbours  (graph concept reranking)
    bias_sources     →  teacher._refill             (source selection order)
    bias_topics      →  homework.generate           (assignment prioritisation)

Config key: attention.bias_strength in config.yaml
  Start at 0.0 (off). Ramp after Pause II baseline is recorded.

Implements IAttention (protocols.py [0]).
"""

from __future__ import annotations

import os
from logger import get_logger

log = get_logger('attention')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

# Source → representative domain keywords for source-level biasing
_SOURCE_DOMAINS: dict[str, list[str]] = {
    'wikipedia':   ['history', 'geography', 'culture', 'science', 'art', 'biology'],
    'simple_wiki': ['basic', 'simple', 'introduction', 'overview'],
    'arxiv':       ['physics', 'math', 'quantum', 'algorithm', 'neural', 'model'],
    'gutenberg':   ['literature', 'novel', 'poem', 'story', 'fiction', 'book'],
    'reddit':      ['community', 'discussion', 'opinion', 'experience', 'social'],
    'openalex':    ['research', 'study', 'paper', 'academic', 'analysis', 'journal'],
    'web':         [],
}


# ─────────────────────────────────────────────────────────── Config read ──

def _strength() -> float:
    """Read bias_strength from config.yaml on every call — no restart needed."""
    try:
        import yaml
        with open(_CFG_PATH, encoding='utf-8') as f:
            return float(yaml.safe_load(f).get('attention', {})
                         .get('bias_strength', 0.0))
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────── Core ──────

def _novelty_strength() -> float:
    """Read attention.novelty_strength from config.yaml live."""
    try:
        import yaml
        with open(_CFG_PATH, encoding='utf-8') as f:
            return float(yaml.safe_load(f).get('attention', {})
                         .get('novelty_strength', 0.0))
    except Exception:
        return 0.0


def bias(candidates: list, scores: list[float],
         meta, strength: float,
         novelty_scores: list[float] | None = None,
         novelty_strength: float = 0.0) -> list:
    """
    Pure reranking function. Returns candidates sorted by biased score.

    Parameters
    ----------
    candidates : list           any items (strings, dicts, …)
    scores     : list[float]    base score parallel to candidates
    meta       : IMetaState | None
    strength   : float          bias multiplier from config

    Returns
    -------
    list   candidates in new order (highest biased score first)
    """
    if not candidates:
        return candidates

    # strength=0 → pure no-op; skip all computation
    if strength == 0.0 or meta is None:
        return list(candidates)

    try:
        active = meta.active(threshold=0.0)   # {text: activation}
    except Exception:
        return list(candidates)

    # s5-6 — hot tension concepts get an extra activation bump
    try:
        from tension import TensionTracker
        _tension = TensionTracker.get()
        hot_set = set(_tension.hot(n=20))
    except Exception:
        hot_set = set()

    biased: list[tuple[float, int]] = []
    for i, (cand, base) in enumerate(zip(candidates, scores)):
        act = active.get(cand, 0.0) if isinstance(cand, str) else 0.0
        if isinstance(cand, str) and cand in hot_set:
            act = min(1.0, act + _tension.mean(cand) * 0.5)
        # B2 — novelty bias term
        n_score = novelty_scores[i] if novelty_scores and i < len(novelty_scores) else 0.0
        new = base * (1.0 + strength * act + novelty_strength * n_score)
        biased.append((new, i))

    # normalise by max so scores stay in a comparable range
    max_score = max(s for s, _ in biased) or 1.0
    biased    = [(s / max_score, i) for s, i in biased]

    biased.sort(key=lambda x: x[0], reverse=True)
    result = [candidates[i] for _, i in biased]

    if any(active.get(c, 0.0) > 0 for c in candidates if isinstance(c, str)):
        log.debug('bias applied: strength=%.2f  top=%s',
                  strength, result[0] if result else None)

    return result


# ────────────────────────────────────────────────── Named call sites ──

def bias_neighbours(neighbours: list[str], scores: list[float],
                    meta, strength: float | None = None) -> list[str]:
    """
    s4-5 call site — modulator._get_neighbours.
    Reranks graph neighbours by edge weight × meta activation × novelty.
    """
    s  = strength if strength is not None else _strength()
    ns = _novelty_strength()
    # B2 — fetch novelty scores for each candidate
    novelty_scores = None
    if ns > 0.0:
        try:
            from novelty import NoveltyTracker
            _nov = NoveltyTracker.get()
            novelty_scores = [_nov.novelty_score(nb) for nb in neighbours]
        except Exception:
            pass
    return bias(neighbours, scores, meta, s,
                novelty_scores=novelty_scores, novelty_strength=ns)


def bias_sources(sources: list[str],
                 meta, strength: float | None = None) -> list[str]:
    """
    s4-3 call site — teacher._refill.
    Reranks source names so that sources whose domain keywords overlap
    with currently active concepts are queried first.
    Base score = 1.0 for all; boosted by domain keyword overlap with active.
    """
    s = strength if strength is not None else _strength()
    if s == 0.0 or meta is None:
        return list(sources)

    try:
        active = meta.active(threshold=0.0)
    except Exception:
        return list(sources)

    # Score each source by sum of activations of domain-keyword-matching concepts
    source_scores: list[float] = []
    for src in sources:
        keywords = _SOURCE_DOMAINS.get(src, [])
        boost = sum(
            act for concept, act in active.items()
            if any(kw in concept.lower() for kw in keywords)
        ) if keywords else 0.0
        source_scores.append(1.0 + boost)

    return bias(sources, source_scores, meta=None, strength=0.0)
    # Note: pass strength=0 here — the scoring already encodes the boost;
    # we just want the sort, not a second multiplication.


def bias_topics(assignments: list[dict],
                meta, strength: float | None = None) -> list[dict]:
    """
    s4-4 call site — homework.generate.
    Reranks topic assignments: base score = (1 - coverage) so that
    least-known topics rank highest, then meta-boosted by whether any
    word in the topic is currently under cognitive pressure.
    """
    s = strength if strength is not None else _strength()
    if not assignments:
        return assignments

    if s == 0.0 or meta is None:
        return sorted(assignments, key=lambda a: a.get('coverage', 0.0))

    try:
        active = meta.active(threshold=0.0)
    except Exception:
        return sorted(assignments, key=lambda a: a.get('coverage', 0.0))

    topics    = [a.get('topic', '') for a in assignments]
    # base score = inverse coverage (least-known = highest score)
    base_scores = [1.0 - a.get('coverage', 0.0) for a in assignments]

    # build per-topic activation: max activation of any word in the topic
    topic_activations: list[float] = []
    for topic in topics:
        words = topic.lower().split()
        act   = max((active.get(w, 0.0) for w in words), default=0.0)
        topic_activations.append(act)

    # manual bias (don't call bias() — we need the activation per topic, not per candidate text)
    biased: list[tuple[float, int]] = []
    for i, (base, act) in enumerate(zip(base_scores, topic_activations)):
        new = base * (1.0 + s * act)
        biased.append((new, i))

    biased.sort(key=lambda x: x[0], reverse=True)
    return [assignments[i] for _, i in biased]
