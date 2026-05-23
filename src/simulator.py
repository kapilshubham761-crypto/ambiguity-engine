"""
Node [Sim] — Internal Simulation Space
========================================
V3 Step 5: Runs hypothetical cognition without committing state.
This is the beginning of imagination and planning.

Mechanism:
    simulate(context_concepts, steps) works as a pure function:
    1. Copy the current working-memory activation snapshot
    2. At each step: predict next concepts (via Predictor [P])
    3. Apply predicted activations to the temporary snapshot
    4. Record the state at each step
    5. Return the full trajectory — no real state is mutated

The simulation reveals probable cognitive trajectories:
    What concepts would the system activate next?
    Which regions would become dominant?
    Would entropy rise or fall?

SimResult schema:
    {
      steps: [
        {
          step:     int
          concepts: list[str]          top active concepts at this step
          new_arrivals: list[str]      concepts that just became active
          predicted:    list[str]      what the predictor suggested
        },
        …
      ],
      total_concepts_touched: int
      terminal_concepts: list[str]    concepts active at final step
    }

Usage:
    sim = Simulator.get()
    result = sim.simulate(["wave", "frequency"], steps=3)

This is pure functional — calling simulate() has zero side effects on
memory, meta_state, or any persistent store.
"""

from __future__ import annotations

import copy
import os
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('simulator')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['Simulator'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('simulator', {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Simulator                                                                    #
# --------------------------------------------------------------------------- #

class Simulator:
    """
    Pure-functional sandbox cognition runner.
    Reads from Predictor [P] and TemporalMemory [Mem] but never writes to them.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._max_steps    = int(cfg.get('max_steps', 5))
        self._branch_factor = int(cfg.get('branch_factor', 3))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def simulate(self, context_concepts: list[str],
                 steps: int | None = None) -> dict:
        """
        Run a hypothetical propagation from context_concepts.

        Parameters
        ----------
        context_concepts : list[str]   starting concept texts
        steps            : int         propagation depth (default: max_steps)

        Returns
        -------
        SimResult dict with full trajectory.
        """
        n_steps = min(steps or self._max_steps, self._max_steps)

        # Snapshot working memory as a mutable scratchpad
        scratchpad = self._working_snapshot()

        # Seed the scratchpad with context
        for c in context_concepts:
            scratchpad[c] = max(scratchpad.get(c, 0.0), 0.5)

        trajectory = []
        all_touched: set[str] = set(context_concepts)
        current_context = list(context_concepts)

        for step in range(1, n_steps + 1):
            predictions = self._predict(current_context)
            new_arrivals = []

            for concept, prob in predictions[:self._branch_factor]:
                boost = prob * 0.5   # damped activation in simulation
                old   = scratchpad.get(concept, 0.0)
                new   = min(1.0, old + boost * (1.0 - old))
                scratchpad[concept] = new
                if concept not in all_touched:
                    new_arrivals.append(concept)
                    all_touched.add(concept)

            # Top active concepts in scratchpad at this step
            top = sorted(scratchpad.items(), key=lambda x: x[1], reverse=True)
            top_concepts = [c for c, _ in top[:10]]

            trajectory.append({
                'step':         step,
                'concepts':     top_concepts,
                'new_arrivals': new_arrivals,
                'predicted':    [c for c, _ in predictions[:self._branch_factor]],
            })

            # Advance context to top active concepts
            current_context = top_concepts[:5]

        terminal = [c for c, _ in sorted(
            scratchpad.items(), key=lambda x: x[1], reverse=True
        )[:10]]

        result = {
            'steps':                  trajectory,
            'total_concepts_touched': len(all_touched),
            'terminal_concepts':      terminal,
            'context_in':             context_concepts,
        }
        log.debug('simulate: context=%s steps=%d touched=%d',
                  context_concepts[:3], n_steps, len(all_touched))
        return result

    def simulate_goal(self, goal: str, steps: int | None = None) -> dict:
        """
        Simulate starting from a goal keyword: find its graph neighbours
        as seeds, then propagate.
        """
        seeds = self._goal_seeds(goal)
        return self.simulate(seeds, steps=steps)

    @classmethod
    def get(cls) -> 'Simulator':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _working_snapshot() -> dict[str, float]:
        """Non-mutating read of current working memory."""
        try:
            from memory import TemporalMemory
            mem = TemporalMemory.get()
            return {c: v for c, v in mem.top_working(50)}
        except Exception:
            return {}

    @staticmethod
    def _predict(context: list[str]) -> list[tuple[str, float]]:
        try:
            from predictor import Predictor
            return Predictor.get().predict(context)
        except Exception:
            return []

    @staticmethod
    def _goal_seeds(goal: str) -> list[str]:
        """Find concept texts related to a goal keyword from active memory."""
        try:
            from memory import TemporalMemory
            working = TemporalMemory.get().top_working(30)
            seeds = [c for c, _ in working if goal.lower() in c.lower()]
            return seeds[:5] if seeds else [goal]
        except Exception:
            return [goal]
