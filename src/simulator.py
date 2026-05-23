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

    def simulate_chains(self, context_concepts: list[str],
                        depth: int = 10, branching: int = 3) -> dict:
        """
        V3-16: Recursive planning tree.

        Builds a tree of simulation branches up to `depth` steps,
        expanding `branching` alternatives at each node.
        Each branch is evaluated by a composite score:
            score = novelty + goal_alignment - entropy_penalty - contradiction_penalty

        Returns the best branch and the full tree.
        """
        depth    = min(depth,     10)
        branching = min(branching, 5)
        predictions = self._predict(context_concepts)
        root_branches = [c for c, _ in predictions[:branching]]
        if not root_branches:
            root_branches = context_concepts[:branching]

        best_branch: dict | None = None
        best_score: float = -1.0
        tree: list[dict] = []

        for seed in root_branches:
            branch_result = self.simulate([seed], steps=depth)
            score = self._evaluate_branch(branch_result)
            entry = {
                'seed':    seed,
                'score':   round(score, 4),
                'result':  branch_result,
            }
            tree.append(entry)
            if score > best_score:
                best_score  = score
                best_branch = entry

        log.debug('simulate_chains: %d branches depth=%d best=%.3f',
                  len(tree), depth, best_score)
        return {
            'context_in':  context_concepts,
            'best_branch': best_branch,
            'all_branches': tree,
            'depth':       depth,
        }

    @staticmethod
    def _evaluate_branch(result: dict) -> float:
        """Score a simulation branch on 4 dimensions."""
        score = 0.0
        terminal = result.get('terminal_concepts', [])
        if not terminal:
            return 0.0

        # Novelty of terminal concepts
        try:
            from novelty import NoveltyTracker
            nt = NoveltyTracker.get()
            avg_novelty = sum(nt.novelty_score(c) for c in terminal[:5]) / min(len(terminal), 5)
            score += avg_novelty * 0.4
        except Exception:
            score += 0.2   # neutral default

        # Goal alignment: terminal concepts match current goal keyword
        try:
            from goals import GoalEngine
            goal = GoalEngine.get().current_goal().replace('_', ' ')
            matches = sum(1 for c in terminal if goal.lower() in c.lower())
            score += (matches / max(len(terminal), 1)) * 0.3
        except Exception:
            pass

        # Contradiction penalty
        try:
            from contradiction import ContradictionRegistry
            cr = ContradictionRegistry.get()
            unresolved = {c['concept_a'] for c in cr.unresolved()}
            overlap = sum(1 for c in terminal if c in unresolved)
            score -= (overlap / max(len(terminal), 1)) * 0.2
        except Exception:
            pass

        # Total diversity bonus
        touched = result.get('total_concepts_touched', 0)
        score += min(touched / 20.0, 0.1)

        return max(0.0, score)

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
