"""
Node [P] — Predictive Activation Engine
==========================================
V3 Step 3: Predicts what concepts should appear next given current context,
then pre-activates them in working memory.

This is the start of anticipatory cognition.

Mechanism:
    Given context_concepts = [c1, c2, …]:
      for each ci: look up transitions_from(ci) → {next: weight}
      aggregate by weighted vote across all context concepts
      normalise to probabilities
      return top_k as predictions

Pre-activation:
    Call pre_activate(context_concepts, memory) to push the top predictions
    into working memory with a reduced gain (predictor.pre_activation_gain).
    This biases attention before retrieval even occurs.

Example:
    context = ["rhythm", "sound"]
    transitions reveal: rhythm→wave (1.8), rhythm→frequency (1.2),
                        sound→wave (2.1), sound→frequency (0.9)
    aggregated: wave=3.9, frequency=2.1
    → both pre-activated before next lesson arrives

Public API:
    predict(context_concepts) → list[(concept, probability)]
    pre_activate(context_concepts, memory) → list[str] pre-activated
    snapshot() → diagnostic dict
"""

from __future__ import annotations

import os
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('predictor')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['Predictor'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('predictor', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Predictor                                                                    #
# --------------------------------------------------------------------------- #

class Predictor:
    """
    Reads transition graph from EpisodeStore [Ep] and generates next-concept
    predictions for a given context.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._top_k        = int(cfg.get('top_k', 5))
        self._pre_gain     = float(cfg.get('pre_activation_gain', 0.08))
        self._min_weight   = float(cfg.get('min_transition_weight', 0.05))
        self._last_predictions: list[tuple[str, float]] = []

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def predict(self, context_concepts: list[str]) -> list[tuple[str, float]]:
        """
        Given a list of current context concepts, aggregate transition
        weights to predict the most likely next concepts.

        Returns list of (concept, probability) sorted by probability desc.
        """
        from episodes import EpisodeStore
        store = EpisodeStore.get()

        # Aggregate votes from all context concepts
        votes: dict[str, float] = {}
        for source in context_concepts:
            trans = store.transitions_from(source, min_weight=self._min_weight)
            for nxt, weight in trans.items():
                if nxt not in context_concepts:   # don't predict what we already have
                    votes[nxt] = votes.get(nxt, 0.0) + weight

        if not votes:
            self._last_predictions = []
            return []

        # Normalise to probabilities
        total = sum(votes.values())
        probs = [(c, round(w / total, 4)) for c, w in votes.items()]
        probs.sort(key=lambda x: x[1], reverse=True)

        self._last_predictions = probs[:self._top_k]
        log.debug('predictor: context=%s → top=%s',
                  context_concepts[:3], self._last_predictions[:3])
        return self._last_predictions

    def pre_activate(self, context_concepts: list[str], memory) -> list[str]:
        """
        Predict and push top concepts into working memory with pre_activation_gain.
        Returns list of pre-activated concept texts.
        """
        predictions = self.predict(context_concepts)
        if not predictions:
            return []

        texts = [c for c, _ in predictions]
        try:
            memory.reinforce(texts, gain_override=self._pre_gain)
        except Exception as e:
            log.debug('pre_activate memory error: %s', e)

        log.debug('pre-activated: %s', texts[:3])
        return texts

    def last_predictions(self) -> list[tuple[str, float]]:
        return list(self._last_predictions)

    def snapshot(self) -> dict:
        from episodes import EpisodeStore
        store = EpisodeStore.get()
        return {
            'top_k':              self._top_k,
            'pre_gain':           self._pre_gain,
            'last_predictions':   self._last_predictions,
            'transition_count':   len(store._edges),
        }

    @classmethod
    def get(cls) -> 'Predictor':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
