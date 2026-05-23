"""
Node [Ec] — Autonomous Cognitive Ecology
==========================================
V4 Step 1: Orchestrates all cognitive subsystems in a single
autonomous tick loop.

The ecology tick is the heartbeat of the full cognitive architecture.
It sequences the subsystems in causal order so that each node's output
can feed the next:

    1. EnergyBudget.replenish()            — restore capacity
    2. PredictionError.observe(...)        — measure how wrong last cycle was
    3. ActiveInference.step()              — decide what to attend to
    4. WorldModel.infer_from_context(...)  — update causal graph
    5. BindingDetector.update()            — refresh cross-modal bridges
    6. MetaLearner.tick()                  — score strategy effectiveness
    7. Evolver.tick()                      — nudge architecture params
    8. SelfModel.tick()                    — update self-prediction
    9. IdentityTracker.observe()           — drift trait profile
   10. GoalEngine.tick()                   — recompute intrinsic drives
   11. ReflectionMonitor.report()          — snapshot cognitive state
   12. CognitiveAgents.arbitrate(...)      — rank current hot concepts
   13. Log ecology state every log_every ticks

Called from teacher.py background loop every tick_every ticks.

Usage:
    from ecology import CognitiveEcology
    ec = CognitiveEcology.get()
    ec.tick(context_concepts)   # pass concepts from last accepted lesson
"""

from __future__ import annotations

import os
import time
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('ecology')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_SINGLETON: Optional['CognitiveEcology'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('ecology', {})
    except Exception:
        return {}


class CognitiveEcology:
    """
    Full-loop autonomous cognitive orchestrator.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._tick_every = int(cfg.get('tick_every', 1))
        self._log_every  = int(cfg.get('log_every',  10))
        self._tick_count: int = 0
        self._last_context: list[str] = []
        self._last_predicted: list[str] = []

    # ------------------------------------------------------------------ #
    # Main tick                                                            #
    # ------------------------------------------------------------------ #

    def tick(self, context_concepts: list[str] | None = None) -> dict:
        """
        Run one ecology cycle.  context_concepts = concepts from the
        most recently accepted lesson (may be empty between lessons).
        Returns a summary dict of what happened this tick.
        """
        self._tick_count += 1
        concepts = list(context_concepts or self._last_context or [])
        summary: dict = {'tick': self._tick_count, 'ts': time.time()}

        # 1. Energy replenish
        try:
            from energy import EnergyBudget
            EnergyBudget.get().replenish()
        except Exception:
            pass

        # 2. Prediction error: compare last predicted vs actual
        try:
            from prediction_error import PredictionError
            pe = PredictionError.get()
            if self._last_predicted and concepts:
                err = pe.observe(self._last_predicted, concepts)
                summary['prediction_error'] = round(err, 4)
        except Exception:
            pass

        # 3. Active inference: determine what to focus on next
        action = None
        try:
            from active_inference import ActiveInference
            action = ActiveInference.get().step()
            if action:
                summary['ai_action'] = action.get('type')
        except Exception:
            pass

        # 4. World model: infer causal edges from context
        try:
            from world_model import WorldModel
            if concepts:
                WorldModel.get().infer_from_context(concepts)
        except Exception:
            pass

        # 5. Binding detector: update cross-modal bridges
        try:
            from binding import BindingDetector
            from energy import EnergyBudget
            if EnergyBudget.get().spend('exploration'):
                bd = BindingDetector.get()
                if concepts:
                    try:
                        from regions import RegionIndex
                        ri = RegionIndex.get()
                        region = ri.active_region() if ri else 'default'
                    except Exception:
                        region = 'default'
                    bd.observe_episode(concepts, region or 'default')
        except Exception:
            pass

        # 6. Meta-learner: score strategies
        try:
            from meta_learning import MetaLearner
            MetaLearner.get().tick()
        except Exception:
            pass

        # 7. Evolver: nudge params
        try:
            from evolver import Evolver
            Evolver.get().tick()
        except Exception:
            pass

        # 8. Self-model: predict own future state
        try:
            from self_model import SelfModel
            SelfModel.get().tick()
        except Exception:
            pass

        # 9. Identity: drift traits
        try:
            from identity import IdentityTracker
            IdentityTracker.get().observe()
        except Exception:
            pass

        # 10. Goal engine
        try:
            from goals import GoalEngine
            GoalEngine.get().tick()
        except Exception:
            pass

        # 11. Reflection snapshot
        try:
            from reflection import ReflectionMonitor
            report = ReflectionMonitor.get().report()
            summary['mode'] = report.get('current_mode')
            summary['entropy'] = report.get('entropy')
        except Exception:
            pass

        # 12. Agent arbitration on current hot concepts
        try:
            from tension import TensionTracker
            from agents import CognitiveAgents
            hot = TensionTracker.get().hot_concepts()
            if hot:
                ranked = CognitiveAgents.get().arbitrate(hot)
                summary['top_arbitrated'] = ranked[:3]
        except Exception:
            pass

        # Update context for next cycle
        if concepts:
            self._last_context = concepts
        # Store current predictions as "last_predicted" for error tracking
        try:
            from predictor import Predictor
            preds = Predictor.get().predict(concepts or self._last_context)
            self._last_predicted = [c for c, _ in preds[:10]]
        except Exception:
            pass

        # Log
        if self._tick_count % self._log_every == 0:
            log.info('ecology tick %d: mode=%s entropy=%s ai=%s',
                     self._tick_count,
                     summary.get('mode', '?'),
                     summary.get('entropy', '?'),
                     summary.get('ai_action', 'none'))

        return summary

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        snaps: dict = {'tick_count': self._tick_count}
        for name, cls_path in [
            ('energy',           ('energy',           'EnergyBudget')),
            ('prediction_error', ('prediction_error', 'PredictionError')),
            ('active_inference', ('active_inference', 'ActiveInference')),
            ('world_model',      ('world_model',      'WorldModel')),
            ('binding',          ('binding',           'BindingDetector')),
            ('meta_learning',    ('meta_learning',     'MetaLearner')),
            ('evolver',          ('evolver',           'Evolver')),
            ('self_model',       ('self_model',        'SelfModel')),
            ('identity',         ('identity',          'IdentityTracker')),
        ]:
            try:
                mod = __import__(cls_path[0])
                obj = getattr(mod, cls_path[1]).get()
                snaps[name] = obj.snapshot()
            except Exception:
                pass
        return snaps

    @classmethod
    def get(cls) -> 'CognitiveEcology':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
