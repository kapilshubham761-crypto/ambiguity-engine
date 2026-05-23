"""
Node [Rf] — Self-Reflection Layer
=====================================
V3 Step 6: The system models its own cognition — structural self-observation,
not philosophical consciousness.

Aggregates live metrics from every cognitive subsystem and exposes a unified
self_report() dict. This report is:
  - Read by GoalEngine [Go] to select the next intrinsic drive
  - Written to logs for analysis / UI display
  - Used by StabilityMonitor [S] to cross-check mode selection

Metrics tracked:
    current_mode        str     from StabilityMonitor
    entropy             float   normalised Shannon entropy
    dominant_region     str|None
    active_concept_count int
    repetition_rate     float   fraction of top-5 repeated across last N snapshots
    ambiguity_load      float   mean tension across hot concepts
    novelty_balance     float   fraction of working memory that is novel (< 2 seen)
    open_contradictions int     from ContradictionRegistry
    semantic_depth      int     concepts in semantic memory
    prediction_accuracy float   (reserved — always 0 until eval harness added)
    goal                str     from GoalEngine (current drive)

The system can detect its own pathological states:
    over_fixating   → entropy < 0.2  (focused on one cluster)
    drifting        → entropy > 0.8  (no coherent attention)
    looping         → repetition_rate > 0.8 (same concepts cycling)
    stuck           → open_contradictions > 5 and no novelty

Persists last report to data/reflection.json (light file, not JSONL).
Singleton via ReflectionMonitor.get().
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from logger import get_logger

log = get_logger('reflection')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_PATH     = os.path.join(_ROOT, 'data', 'reflection.json')

_SINGLETON: Optional['ReflectionMonitor'] = None


# --------------------------------------------------------------------------- #
# ReflectionMonitor                                                            #
# --------------------------------------------------------------------------- #

class ReflectionMonitor:
    """
    Reads from all cognitive subsystems and produces a unified self-report.
    Non-invasive: only reads, never writes to other subsystems.
    """

    def __init__(self) -> None:
        self._last_report: dict = {}

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def report(self) -> dict:
        """Compute and return the full self-report dict. Persists to file."""
        r: dict = {
            'ts': datetime.now(tz=timezone.utc).isoformat(timespec='seconds'),
        }

        # Stability / mode
        r.update(self._stability_metrics())

        # Active concepts
        r.update(self._meta_metrics())

        # Novelty balance
        r.update(self._novelty_metrics())

        # Tension / ambiguity load
        r.update(self._tension_metrics())

        # Contradictions
        r.update(self._contradiction_metrics())

        # Semantic depth
        r.update(self._memory_metrics())

        # Goal
        r.update(self._goal_metrics())

        # Pathology flags
        r['flags'] = self._flags(r)

        self._last_report = r
        self._save(r)
        log.debug('reflection: mode=%s entropy=%.2f flags=%s',
                  r.get('current_mode'), r.get('entropy', 0.0), r.get('flags'))
        return r

    def describe(self) -> str:
        """One-line natural language summary of current cognitive state."""
        r = self._last_report or self.report()
        mode    = r.get('current_mode', 'reflective')
        flags   = r.get('flags', [])
        entropy = r.get('entropy', 0.0)
        n_act   = r.get('active_concept_count', 0)
        goal    = r.get('goal', 'none')

        desc = f"Mode: {mode} | Entropy: {entropy:.2f} | Active: {n_act}"
        if flags:
            desc += f" | Flags: {', '.join(flags)}"
        desc += f" | Drive: {goal}"
        return desc

    def last(self) -> dict:
        return dict(self._last_report)

    @classmethod
    def get(cls) -> 'ReflectionMonitor':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Subsystem reads                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _stability_metrics() -> dict:
        try:
            from stability import StabilityMonitor
            sm = StabilityMonitor.get()
            return {
                'current_mode': sm._current_mode,
                'entropy':      round(sm.entropy(), 4),
                'mean_entropy': round(sm.mean_entropy(), 4),
                'is_stable':    sm.is_stable(),
            }
        except Exception:
            return {'current_mode': 'unknown', 'entropy': 0.0,
                    'mean_entropy': 0.0, 'is_stable': True}

    @staticmethod
    def _meta_metrics() -> dict:
        try:
            from meta_state import MetaState
            ms = MetaState.get()
            snap = ms.snapshot()
            return {
                'active_concept_count': snap.get('active_count', 0),
                'dominant_region':      snap.get('active_region'),
                'region_count':         snap.get('region_count', 0),
            }
        except Exception:
            return {'active_concept_count': 0, 'dominant_region': None, 'region_count': 0}

    @staticmethod
    def _novelty_metrics() -> dict:
        try:
            from novelty import NoveltyTracker
            from memory import TemporalMemory
            nov = NoveltyTracker.get()
            mem = TemporalMemory.get()
            working = mem.top_working(20)
            if not working:
                return {'novelty_balance': 0.0, 'repetition_rate': 0.0}
            novel = sum(1 for c, _ in working if nov.times_seen(c) <= 2)
            nov_bal = round(novel / len(working), 4)
            is_loop = nov.check_loop()
            rep_rate = 0.9 if is_loop else 0.0
            return {'novelty_balance': nov_bal, 'repetition_rate': rep_rate}
        except Exception:
            return {'novelty_balance': 0.0, 'repetition_rate': 0.0}

    @staticmethod
    def _tension_metrics() -> dict:
        try:
            from tension import TensionTracker
            tt = TensionTracker.get()
            hot = tt.hot(n=10)
            if not hot:
                return {'ambiguity_load': 0.0, 'hot_count': 0}
            load = round(sum(tt.mean(c) for c in hot) / len(hot), 4)
            return {'ambiguity_load': load, 'hot_count': len(hot)}
        except Exception:
            return {'ambiguity_load': 0.0, 'hot_count': 0}

    @staticmethod
    def _contradiction_metrics() -> dict:
        try:
            from contradiction import ContradictionRegistry
            snap = ContradictionRegistry.get().snapshot()
            return {
                'open_contradictions':  snap.get('open', 0),
                'total_contradictions': snap.get('total', 0),
            }
        except Exception:
            return {'open_contradictions': 0, 'total_contradictions': 0}

    @staticmethod
    def _memory_metrics() -> dict:
        try:
            from memory import TemporalMemory
            snap = TemporalMemory.get().snapshot()
            return {
                'semantic_depth':  snap.get('semantic_count', 0),
                'episodic_count':  snap.get('episodic_count', 0),
                'working_count':   snap.get('working_count', 0),
            }
        except Exception:
            return {'semantic_depth': 0, 'episodic_count': 0, 'working_count': 0}

    @staticmethod
    def _goal_metrics() -> dict:
        try:
            from goals import GoalEngine
            ge = GoalEngine.get()
            return {
                'goal':        ge.current_goal(),
                'drive_scores': ge.drive_scores(),
            }
        except Exception:
            return {'goal': 'none', 'drive_scores': {}}

    # ------------------------------------------------------------------ #
    # Pathology detection                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _flags(r: dict) -> list[str]:
        flags = []
        entropy        = r.get('entropy', 0.5)
        rep_rate       = r.get('repetition_rate', 0.0)
        contradictions = r.get('open_contradictions', 0)
        nov_balance    = r.get('novelty_balance', 0.5)

        if entropy < 0.20:
            flags.append('over_fixating')
        if entropy > 0.80:
            flags.append('drifting')
        if rep_rate > 0.80:
            flags.append('looping')
        if contradictions > 5 and nov_balance < 0.10:
            flags.append('stuck')
        return flags

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _save(self, report: dict) -> None:
        dir_ = os.path.dirname(_PATH)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, _PATH)
