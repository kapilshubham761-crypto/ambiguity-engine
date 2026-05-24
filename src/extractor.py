"""
Node [E] — Extractor
=====================
Converts a sentence into an embedded Concept.

No NLP preprocessing — raw sentences feed directly into the graph.
The engine decides what matters through activation, tension, and graph structure.
"""

from __future__ import annotations

from typing import NamedTuple

import torch  # must be first — prevents circular import via sentence_transformers
from sentence_transformers import SentenceTransformer

from logger import get_logger

log = get_logger('extractor')

EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
EMBEDDING_DIM   = 384
_MIN_LEN        = 10   # characters — anything shorter skipped


class Concept(NamedTuple):
    text:      str
    embedding: list[float]
    source:    str


_encoder: SentenceTransformer | None = None
_embed_cache: dict[str, list[float]] = {}


def _get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        log.info('Loading sentence-transformer %s …', EMBEDDING_MODEL)
        _encoder = SentenceTransformer(EMBEDDING_MODEL)
    return _encoder


def _embed(texts: list[str]) -> dict[str, list[float]]:
    """Batch embed, using session cache for seen texts."""
    to_encode = [t for t in texts if t not in _embed_cache]
    if to_encode:
        vecs = _get_encoder().encode(to_encode, show_progress_bar=False)
        for text, vec in zip(to_encode, vecs):
            _embed_cache[text] = vec.tolist()
    return {t: _embed_cache[t] for t in texts}


def extract(text: str) -> list[Concept]:
    """
    Embed a sentence as a single Concept.
    Returns empty list if the sentence is too short.
    """
    text = text.strip()
    if len(text) < _MIN_LEN:
        return []
    key = text[:500]
    emb = _embed([key])
    return [Concept(text=key, embedding=emb[key], source='sentence')]


def extract_batch(sentences: list[str]) -> list[list[Concept]]:
    """
    Embed a whole article's sentences in one encoder call — much faster
    than calling extract() per sentence when many are cache misses.
    Returns a list of Concept lists (one per input sentence).
    """
    cleaned = [s.strip()[:500] for s in sentences]
    valid   = [(i, s) for i, s in enumerate(cleaned) if len(s) >= _MIN_LEN]

    if not valid:
        return [[] for _ in sentences]

    texts = [s for _, s in valid]
    emb   = _embed(texts)

    result: list[list[Concept]] = [[] for _ in sentences]
    for (i, s) in valid:
        result[i] = [Concept(text=s, embedding=emb[s], source='sentence')]
    return result


def cache_size() -> int:
    return len(_embed_cache)
