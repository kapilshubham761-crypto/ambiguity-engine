"""
Phase 2: Concept Extraction + Embedding Pipeline.

Extracts concepts from raw text via spaCy (noun chunks + named entities),
normalises them, and attaches 384-dim MiniLM embeddings. An in-memory cache
prevents re-embedding concepts seen in the same session.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import spacy
from sentence_transformers import SentenceTransformer

from logger import get_logger

log = get_logger('extractor')

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
EMBEDDING_DIM   = 384

# Terms so generic they add no semantic signal
_GENERIC = {
    'thing', 'things', 'way', 'ways', 'people', 'person', 'use', 'lot',
    'kind', 'type', 'part', 'fact', 'place', 'point', 'example', 'time',
    'times', 'case', 'cases', 'aspect', 'issue', 'issues', 'question',
    'something', 'someone', 'anything', 'everything', 'nothing', 'number',
    'amount', 'level', 'set', 'group', 'series', 'form', 'end', 'side',
    'sense', 'term', 'terms', 'result', 'results', 'area', 'areas',
}

# Named entity labels we care about
_ENTITY_LABELS = {'PERSON', 'ORG', 'GPE', 'LOC', 'PRODUCT', 'EVENT', 'WORK_OF_ART', 'FAC'}

# --------------------------------------------------------------------------- #
# Data type                                                                    #
# --------------------------------------------------------------------------- #

class Concept(NamedTuple):
    text: str               # normalised surface form
    embedding: list[float]  # 384-dim vector
    source: str             # 'chunk' | 'entity'


# --------------------------------------------------------------------------- #
# Module-level singletons (loaded once per process)                            #
# --------------------------------------------------------------------------- #

_nlp: spacy.language.Language | None = None
_encoder: SentenceTransformer | None = None
_embed_cache: dict[str, list[float]] = {}


def _get_nlp() -> spacy.language.Language:
    global _nlp
    if _nlp is None:
        log.info('Loading spaCy model en_core_web_sm …')
        _nlp = spacy.load('en_core_web_sm')
    return _nlp


def _get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        log.info('Loading sentence-transformer %s …', EMBEDDING_MODEL)
        _encoder = SentenceTransformer(EMBEDDING_MODEL)
    return _encoder


# --------------------------------------------------------------------------- #
# Normalisation helpers                                                        #
# --------------------------------------------------------------------------- #

def _normalise(text: str, doc_vocab) -> str | None:
    """
    Lowercase, lemmatise tokens, strip stopwords and punctuation.
    Returns None if the result is empty or too short.
    """
    tokens = []
    for tok in doc_vocab:
        if tok.is_stop or tok.is_punct or tok.is_space:
            continue
        lemma = tok.lemma_.lower().strip()
        if lemma and re.match(r'^[a-z][a-z0-9 _-]*$', lemma):
            tokens.append(lemma)

    result = ' '.join(tokens).strip()
    return result if len(result) >= 2 else None


def _is_generic(text: str) -> bool:
    return text in _GENERIC or len(text) <= 1


# --------------------------------------------------------------------------- #
# Embedding                                                                    #
# --------------------------------------------------------------------------- #

def _embed(texts: list[str]) -> dict[str, list[float]]:
    """Embed a batch of texts, using the session cache where possible."""
    to_encode = [t for t in texts if t not in _embed_cache]
    if to_encode:
        encoder = _get_encoder()
        vectors = encoder.encode(to_encode, show_progress_bar=False)
        for text, vec in zip(to_encode, vectors):
            _embed_cache[text] = vec.tolist()
    return {t: _embed_cache[t] for t in texts}


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def extract(text: str) -> list[Concept]:
    """
    Extract, normalise, deduplicate, and embed concepts from *text*.
    Returns a list of Concept objects, ordered by appearance.
    """
    nlp = _get_nlp()
    doc = nlp(text)

    raw: list[tuple[str, str]] = []  # (surface, source)

    # 2.1a — noun chunks
    for chunk in doc.noun_chunks:
        norm = _normalise(chunk.text, chunk)
        if norm:
            raw.append((norm, 'chunk'))

    # 2.1b — named entities
    for ent in doc.ents:
        if ent.label_ in _ENTITY_LABELS:
            norm = ent.text.lower().strip()
            norm = re.sub(r'\s+', ' ', norm)
            if norm and len(norm) >= 2:
                raw.append((norm, 'entity'))

    # 2.1c / 2.1d — deduplicate and filter generics
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for text_norm, src in raw:
        if text_norm not in seen and not _is_generic(text_norm):
            seen.add(text_norm)
            unique.append((text_norm, src))

    if not unique:
        log.debug('No concepts extracted from input.')
        return []

    # 2.2b — batch embed
    terms = [t for t, _ in unique]
    embeddings = _embed(terms)

    concepts = [
        Concept(text=t, embedding=embeddings[t], source=src)
        for t, src in unique
    ]

    log.debug('Extracted %d concepts (cache size: %d)', len(concepts), len(_embed_cache))
    return concepts


def cache_size() -> int:
    return len(_embed_cache)
