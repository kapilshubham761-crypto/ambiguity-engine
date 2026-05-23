"""
Node [Ag] — Multi-Agent Internal Cognition
===========================================
V3 Step 9: Parallel competing viewpoints inside one system.

Four sub-agents with different attention biases:
    explorer    high novelty + curiosity; finds unfamiliar territory
    skeptic     high tension; challenges assumptions, flags contradictions
    optimizer   balanced; prefers high-value, well-connected concepts
    stabilizer  high stability; anchors to known, settled knowledge

Each agent scores a set of concept candidates differently.
Arbitration merges scores by weighted vote into a final ranking.

This creates internal debate — the same input is interpreted through
4 lenses simultaneously, and the merged output is richer than any
single perspective.

Usage:
    from agents import CognitiveAgents
    ag = CognitiveAgents.get()
    ranked = ag.arbitrate(candidates, context_meta, context_memory)
    mode_agent = ag.dominant_agent()   # which agent is currently strongest
"""

from __future__ import annotations

import os
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('agents')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['CognitiveAgents'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('agents', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# SubAgent                                                                     #
# --------------------------------------------------------------------------- #

class SubAgent:
    """One competing cognitive perspective with fixed bias weights."""

    def __init__(self, name: str, biases: dict) -> None:
        self.name     = name
        self._novelty    = float(biases.get('novelty',    1.0))
        self._tension    = float(biases.get('tension',    1.0))
        self._stability  = float(biases.get('stability',  1.0))
        self._curiosity  = float(biases.get('curiosity',  1.0))

    def score(self, concept: str, meta, memory, tension_tracker,
              novelty_tracker) -> float:
        """
        Score a single concept from this agent's perspective.
        Returns an unnormalised score ≥ 0.
        """
        act_score  = 0.0
        nov_score  = 0.0
        ten_score  = 0.0
        stab_score = 0.0

        # Meta activation → stability signal
        try:
            active = meta.active(threshold=0.0)
            act    = float(active.get(concept, 0.0))
            act_score  = act * self._stability
        except Exception:
            pass

        # Novelty → novelty + curiosity signal
        try:
            nov        = float(novelty_tracker.novelty_score(concept))
            nov_score  = nov * self._novelty + nov * self._curiosity * 0.5
        except Exception:
            nov_score = self._novelty * 0.5   # default half for unseen

        # Tension (ambiguity load) → skeptic + optimizer signal
        try:
            ten       = float(tension_tracker.mean(concept))
            ten_score = ten * self._tension
        except Exception:
            pass

        # Semantic memory → stability
        try:
            sem   = float(memory.semantic_value(concept))
            stab_score = sem * self._stability
        except Exception:
            pass

        return round(act_score + nov_score + ten_score + stab_score, 6)


# --------------------------------------------------------------------------- #
# CognitiveAgents                                                              #
# --------------------------------------------------------------------------- #

class CognitiveAgents:
    """
    Four sub-agents that collectively rank concept candidates.
    arbitrate() returns candidates sorted by weighted-vote score.
    """

    def __init__(self) -> None:
        cfg  = _cfg()
        biases = cfg.get('sub_agents', {})
        weights = cfg.get('arbitration_weights', {})

        self._agents = {
            name: SubAgent(name, b)
            for name, b in biases.items()
        }
        self._weights = {
            name: float(weights.get(name, 0.25))
            for name in self._agents
        }
        self._last_dominant: str = 'optimizer'

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def arbitrate(self, candidates: list[str], meta=None,
                  memory=None, tension_tracker=None,
                  novelty_tracker=None) -> list[str]:
        """
        Score each candidate through all 4 lenses and return candidates
        sorted by arbitrated score (highest first).
        """
        if not candidates:
            return candidates

        # Lazy singletons if not supplied
        if meta is None:
            try:
                from meta_state import MetaState
                meta = MetaState.get()
            except Exception:
                pass
        if memory is None:
            try:
                from memory import TemporalMemory
                memory = TemporalMemory.get()
            except Exception:
                pass
        if tension_tracker is None:
            try:
                from tension import TensionTracker
                tension_tracker = TensionTracker.get()
            except Exception:
                pass
        if novelty_tracker is None:
            try:
                from novelty import NoveltyTracker
                novelty_tracker = NoveltyTracker.get()
            except Exception:
                pass

        # Score each candidate per agent
        agent_scores: dict[str, dict[str, float]] = {}
        for name, agent in self._agents.items():
            agent_scores[name] = {
                c: agent.score(c, meta, memory, tension_tracker, novelty_tracker)
                for c in candidates
            }

        # Weighted vote merge
        final_scores: dict[str, float] = {}
        for c in candidates:
            total = 0.0
            for name, w in self._weights.items():
                total += w * agent_scores.get(name, {}).get(c, 0.0)
            final_scores[c] = round(total, 6)

        # Track dominant agent (highest mean score this round)
        agent_means = {
            name: sum(agent_scores[name].values()) / max(len(candidates), 1)
            for name in self._agents
        }
        if agent_means:
            self._last_dominant = max(agent_means, key=lambda n: agent_means[n])

        result = sorted(candidates, key=lambda c: final_scores.get(c, 0.0), reverse=True)
        log.debug('agents: arbitrated %d candidates dominant=%s',
                  len(candidates), self._last_dominant)
        return result

    def dominant_agent(self) -> str:
        """Which sub-agent currently produces the highest scores."""
        return self._last_dominant

    def agent_names(self) -> list[str]:
        return list(self._agents.keys())

    def snapshot(self) -> dict:
        return {
            'agents':         list(self._agents.keys()),
            'weights':        self._weights,
            'dominant_agent': self._last_dominant,
        }

    @classmethod
    def get(cls) -> 'CognitiveAgents':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
