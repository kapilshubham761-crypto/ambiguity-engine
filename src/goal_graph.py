"""
Goal Persistence Layer — GoalGraph
====================================
Goals that survive cycles, compete, merge, decay, mutate, and abstract.

This is where proto-agency begins. Goals are not reactive flags — they are
persistent drives with weight, origin, emotional pressure, time horizon,
dependency chains, and contradiction scores.

Goal lifecycle:
    born → active → dormant → abstracted / dissolved

Operations per tick (90s):
    decay()    — reduce weight by time × decay_rate
    compete()  — normalize weights; suppress low-weight goals
    merge()    — find semantically similar pairs → create abstract parent
    mutate()   — small random weight/pressure perturbation by field state
    dissolve() — remove goals below threshold or past time_horizon

Goal schema:
    {
      id:                 str    UUID
      text:               str    natural language goal statement
      weight:             float  [0, 1]  current priority
      origin:             str    regulation | user | abstraction | mutation | merge
      emotional_pressure: float  [0, 1]  urgency / salience
      time_horizon:       float  seconds from birth before natural expiry (0 = eternal)
      dependency_chain:   list[str]  goal ids this depends on
      contradiction_score: float  [0, 1]  conflict with other active goals
      birth_ts:           float  unix timestamp
      last_active_ts:     float
      activation_count:   int
      abstracted_from:    list[str]  child goal ids (if this is a merge result)
      status:             str    active | dormant | abstracted | dissolved
      mode_context:       str    cognitive mode when born
    }

Public API:
    GoalGraph.get()                           singleton
    add(text, origin, weight, **kwargs)       register new goal → GoalNode dict
    activate(goal_id)                         signal goal was exercised
    tick(field_snapshot, mode, goal_str)      one regulation pass
    active_goals() → list[dict]               goals with status=active, sorted by weight
    top(n) → list[dict]                       top-n by weight
    snapshot() → dict                         summary for UI / server
"""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
import time
import uuid
from typing import Optional

from logger import get_logger

log = get_logger('goal_graph')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_DATA_PATH = os.path.join(_ROOT, 'data', 'goal_graph.json')

_SINGLETON: Optional['GoalGraph'] = None

# ── Constants ──────────────────────────────────────────────────────────────────
_MAX_GOALS         = 64
_DISSOLVE_WEIGHT   = 0.02      # below this → dissolved
_DORMANT_WEIGHT    = 0.10      # below this → dormant (not active)
_DECAY_RATE        = 0.994     # per-tick weight multiplier
_MERGE_SIM_THRESH  = 0.72      # cosine similarity threshold for merge candidates
_MUTATION_SIGMA    = 0.04      # std dev of weight perturbation on mutate
_TICK_INTERVAL     = 90        # seconds between ticks


# ── Seed goals that always exist ───────────────────────────────────────────────
_SEED_GOALS = [
    ('resolve the dominant contradiction in the field',    'regulation', 0.60),
    ('expand knowledge into low-coherence regions',        'regulation', 0.55),
    ('consolidate related concepts into stable abstractions', 'regulation', 0.50),
    ('stabilise high-entropy activation patterns',         'regulation', 0.45),
    ('explore novel concepts at the periphery',            'regulation', 0.40),
]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _now() -> float:
    return time.time()


# ══════════════════════════════════════════════════════════════════════════════
# GoalGraph
# ══════════════════════════════════════════════════════════════════════════════

class GoalGraph:

    def __init__(self) -> None:
        self._goals: dict[str, dict] = {}
        self._tick_count = 0
        self._last_tick  = 0.0
        self._load()
        if not self._goals:
            self._seed()

    # ── Public API ─────────────────────────────────────────────────────────────

    def add(self, text: str, origin: str, weight: float = 0.5,
            emotional_pressure: float = 0.5,
            time_horizon: float = 0.0,
            dependency_chain: Optional[list] = None,
            mode_context: str = 'associative') -> dict:
        node = {
            'id':                 str(uuid.uuid4()),
            'text':               text,
            'weight':             _clamp(weight),
            'origin':             origin,
            'emotional_pressure': _clamp(emotional_pressure),
            'time_horizon':       float(time_horizon),
            'dependency_chain':   dependency_chain or [],
            'contradiction_score': 0.0,
            'birth_ts':           _now(),
            'last_active_ts':     _now(),
            'activation_count':   0,
            'abstracted_from':    [],
            'status':             'active',
            'mode_context':       mode_context,
        }
        self._goals[node['id']] = node
        self._trim()
        log.debug('goal added: "%s" (origin=%s weight=%.2f)', text[:60], origin, weight)
        return node

    def activate(self, goal_id: str) -> None:
        g = self._goals.get(goal_id)
        if g:
            g['activation_count'] += 1
            g['last_active_ts']    = _now()
            g['weight']            = _clamp(g['weight'] * 1.05 + 0.02)
            if g['status'] == 'dormant':
                g['status'] = 'active'

    def tick(self, field_snapshot: dict, mode: str, goal_str: str) -> None:
        self._tick_count += 1
        self._last_tick = _now()

        self._decay()
        self._update_from_regulation(mode, goal_str, field_snapshot)
        self._update_contradiction_scores()
        self._compete()
        self._mutate(field_snapshot)
        self._maybe_merge()
        self._dissolve()
        self._trim()
        self._save()

        log.debug('goal_graph tick %d: %d active / %d total',
                  self._tick_count,
                  len([g for g in self._goals.values() if g['status'] == 'active']),
                  len(self._goals))

    def active_goals(self) -> list[dict]:
        return sorted(
            [g for g in self._goals.values() if g['status'] == 'active'],
            key=lambda g: g['weight'],
            reverse=True,
        )

    def top(self, n: int = 5) -> list[dict]:
        return self.active_goals()[:n]

    def all_goals(self) -> list[dict]:
        return list(self._goals.values())

    def snapshot(self) -> dict:
        active = self.active_goals()
        return {
            'tick_count':    self._tick_count,
            'total_goals':   len(self._goals),
            'active_count':  len(active),
            'dormant_count': len([g for g in self._goals.values() if g['status'] == 'dormant']),
            'top_goals':     active[:8],
            'last_tick':     self._last_tick,
        }

    @classmethod
    def get(cls) -> 'GoalGraph':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ── Internal operations ────────────────────────────────────────────────────

    def _decay(self) -> None:
        for g in self._goals.values():
            if g['status'] == 'dissolved':
                continue
            g['weight'] = _clamp(g['weight'] * _DECAY_RATE)
            # time_horizon expiry
            if g['time_horizon'] > 0:
                age = _now() - g['birth_ts']
                if age > g['time_horizon']:
                    g['status'] = 'dissolved'

    def _update_from_regulation(self, mode: str, goal_str: str,
                                field_snapshot: dict) -> None:
        tension   = field_snapshot.get('field_stats', {}).get('mean_tension', 0.0)
        coherence = field_snapshot.get('coherence', 0.0)
        pressure  = field_snapshot.get('pressure', 0.3)

        for g in self._goals.values():
            if g['status'] == 'dissolved':
                continue

            text = g['text'].lower()
            boost = 0.0

            # Boost goals aligned with current regulation goal
            if goal_str in ('resolve_tension', 'stabilise') and 'contradiction' in text:
                boost += 0.04 * tension
            if goal_str == 'expand_knowledge' and 'expand' in text:
                boost += 0.04 * (1.0 - coherence)
            if goal_str == 'consolidate' and 'consolidat' in text:
                boost += 0.03 * coherence
            if goal_str == 'explore' and 'explor' in text:
                boost += 0.03

            # emotional pressure tracks field pressure
            g['emotional_pressure'] = _clamp(
                g['emotional_pressure'] * 0.85 + pressure * 0.15
            )
            g['weight'] = _clamp(g['weight'] + boost)
            g['mode_context'] = mode

    def _update_contradiction_scores(self) -> None:
        active = self.active_goals()
        for g in active:
            score = 0.0
            for other in active:
                if other['id'] == g['id']:
                    continue
                # Rough heuristic: opposite keywords → contradiction
                g_words   = set(g['text'].lower().split())
                o_words   = set(other['text'].lower().split())
                overlap   = len(g_words & o_words) / max(len(g_words | o_words), 1)
                if overlap < 0.1 and other['weight'] > 0.4:
                    score += 0.1
            g['contradiction_score'] = _clamp(score)

    def _compete(self) -> None:
        active = self.active_goals()
        if len(active) < 2:
            return
        total = sum(g['weight'] for g in active)
        if total <= 0:
            return
        # Softmax-style competition: boost top, suppress bottom
        for g in active:
            share = g['weight'] / total
            # Winners gain a little, losers lose a little
            delta = (share - 1.0 / len(active)) * 0.05
            g['weight'] = _clamp(g['weight'] + delta)

        # Demote goals that fell below dormant threshold
        for g in self._goals.values():
            if g['status'] == 'active' and g['weight'] < _DORMANT_WEIGHT:
                g['status'] = 'dormant'
            elif g['status'] == 'dormant' and g['weight'] >= _DORMANT_WEIGHT + 0.05:
                g['status'] = 'active'

    def _mutate(self, field_snapshot: dict) -> None:
        entropy  = field_snapshot.get('entropy', 0.5)
        pressure = field_snapshot.get('pressure', 0.3)
        sigma    = _MUTATION_SIGMA * (0.5 + entropy * 0.5 + pressure * 0.3)

        for g in self._goals.values():
            if g['status'] != 'active':
                continue
            if random.random() > 0.15:
                continue
            delta = random.gauss(0, sigma)
            g['weight']            = _clamp(g['weight'] + delta)
            g['emotional_pressure'] = _clamp(g['emotional_pressure'] + delta * 0.5)

    def _maybe_merge(self) -> None:
        active = self.active_goals()
        if len(active) < 4:
            return
        # Try embedding-based merge for top goals
        try:
            from embed_index import EmbeddingIndex
            idx = EmbeddingIndex.get()
        except Exception:
            return

        merged_ids: set[str] = set()
        new_goals: list[dict] = []

        for i, ga in enumerate(active[:10]):
            if ga['id'] in merged_ids:
                continue
            va = idx.get_embedding(ga['text'])
            if va is None:
                continue
            for gb in active[i+1:10]:
                if gb['id'] in merged_ids:
                    continue
                vb = idx.get_embedding(gb['text'])
                if vb is None:
                    continue
                # Cosine similarity
                denom = (float((va**2).sum())**0.5) * (float((vb**2).sum())**0.5)
                if denom < 1e-8:
                    continue
                sim = float((va * vb).sum()) / denom
                if sim >= _MERGE_SIM_THRESH:
                    # Merge: create abstract parent goal
                    merged_text  = f"[abstract] {ga['text'][:40]} / {gb['text'][:40]}"
                    merged_weight = (ga['weight'] + gb['weight']) / 2.0 * 1.1
                    parent = self.add(
                        text               = merged_text,
                        origin             = 'merge',
                        weight             = merged_weight,
                        emotional_pressure = max(ga['emotional_pressure'], gb['emotional_pressure']),
                        dependency_chain   = [ga['id'], gb['id']],
                    )
                    parent['abstracted_from'] = [ga['id'], gb['id']]
                    ga['status'] = 'abstracted'
                    gb['status'] = 'abstracted'
                    merged_ids.add(ga['id'])
                    merged_ids.add(gb['id'])
                    log.debug('goals merged → "%s"', merged_text[:60])
                    break

    def _dissolve(self) -> None:
        for g in self._goals.values():
            if g['status'] in ('dissolved', 'abstracted'):
                continue
            if g['weight'] < _DISSOLVE_WEIGHT:
                g['status'] = 'dissolved'

    def _trim(self) -> None:
        if len(self._goals) <= _MAX_GOALS:
            return
        by_weight = sorted(self._goals.values(),
                           key=lambda g: (g['status'] == 'dissolved', -g['weight']))
        keep = {g['id'] for g in by_weight[:_MAX_GOALS]}
        self._goals = {gid: g for gid, g in self._goals.items() if gid in keep}

    def _seed(self) -> None:
        for text, origin, weight in _SEED_GOALS:
            self.add(text=text, origin=origin, weight=weight,
                     time_horizon=0.0, emotional_pressure=0.5)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save(self) -> None:
        os.makedirs(os.path.dirname(_DATA_PATH), exist_ok=True)
        payload = {
            'goals':      list(self._goals.values()),
            'tick_count': self._tick_count,
            'saved_at':   _now(),
        }
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_DATA_PATH), suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, _DATA_PATH)
        except Exception as e:
            log.debug('goal_graph save error: %s', e)

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                data = json.load(f)
            for g in data.get('goals', []):
                self._goals[g['id']] = g
            self._tick_count = int(data.get('tick_count', 0))
            log.debug('goal_graph loaded: %d goals', len(self._goals))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('goal_graph load error: %s', e)
