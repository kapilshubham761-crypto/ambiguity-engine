"""
Regulation Engine — Layer 4
============================
Single 60-second tick that reads the JAM field state and adjusts
three global scalars. Replaces:
    MetaRegulator, StabilityMonitor, GoalEngine, EnergyBudget,
    MetaLearner, Evolver, SelfModel, IdentityTracker, ReflectionMonitor

Regulated scalars (written to data/regulation.json):
    gain_rate          activation gain per observation [0.10 – 0.50]
    decay_rate         per-minute activation decay    [0.990 – 0.999]
    diffusion_strength fraction propagated to neighbours [0.05 – 0.25]

Derived state (also in regulation.json):
    mode    focused | exploratory | conflicted | saturated | drifting
    goal    consolidate | expand | resolve | stabilise | explore
    entropy Shannon entropy of activation distribution
    pressure overall field pressure [0, 1]

The mode + goal are written to data/cog_status.json so the overlay
and sidebar still work without changes.

Public API
----------
Regulation.get()          → singleton
regulation.tick(field)    → one regulation pass (called every 60s)
regulation.snapshot()     → current state dict
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Optional, TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    from jam_field import JamField

log = get_logger('regulation')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_REG_PATH  = os.path.join(_ROOT, 'data', 'regulation.json')
_COG_PATH  = os.path.join(_ROOT, 'data', 'cog_status.json')

# ── Scalar bounds ──────────────────────────────────────────────────────────────
_GAIN_BOUNDS       = (0.10, 0.50)
_DECAY_BOUNDS      = (0.990, 0.9990)
_DIFFUSION_BOUNDS  = (0.05, 0.25)

# ── Default scalars ────────────────────────────────────────────────────────────
_DEFAULT_GAIN      = 0.30
_DEFAULT_DECAY     = 0.9972
_DEFAULT_DIFFUSION = 0.12

_SINGLETON: Optional['Regulation'] = None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class Regulation:

    def __init__(self) -> None:
        self._gain      = _DEFAULT_GAIN
        self._decay     = _DEFAULT_DECAY
        self._diffusion = _DEFAULT_DIFFUSION
        self._mode      = 'exploring'
        self._goal      = 'expand_knowledge'
        self._entropy   = 0.0
        self._pressure  = 0.0
        self._tick_count = 0
        self._load()

    # ── Main tick ─────────────────────────────────────────────────────────────

    def tick(self, field: 'JamField') -> None:
        """Read field state, adjust scalars, write outputs."""
        try:
            snap = field.snapshot()
            self._tick_count += 1

            active_count  = snap.get('active_count', 0)
            field_size    = snap.get('field_size', 1)
            coherence     = snap.get('coherence', 0.0)
            stats         = snap.get('field_stats', {})
            mean_act      = stats.get('mean_activation', 0.0)
            mean_novelty  = stats.get('mean_novelty', 0.5)
            mean_tension  = stats.get('mean_tension', 0.0)
            mean_stability = stats.get('mean_stability', 0.5)

            # ── Entropy of activation distribution ───────────────────────────
            top20 = field.top(20, by='activation')
            acts  = [v.get('activation', 0.0) for _, v in top20]
            total = sum(acts) + 1e-8
            probs = [a / total for a in acts if a > 0]
            entropy = -sum(p * math.log2(p) for p in probs if p > 0)
            entropy_norm = entropy / math.log2(max(len(probs), 2))  # [0, 1]

            # ── Field pressure ────────────────────────────────────────────────
            # High when: many active nodes + high tension + high entropy
            saturation = min(active_count / max(field_size, 1) * 20, 1.0)
            pressure   = (saturation * 0.3 + mean_tension * 0.4 + entropy_norm * 0.3)

            # ── Mode election ─────────────────────────────────────────────────
            if coherence > 0.40:
                mode = 'focused'
            elif mean_tension > 0.55:
                mode = 'conflicted'
            elif saturation > 0.70:
                mode = 'saturated'
            elif entropy_norm > 0.75:
                mode = 'exploratory'
            elif mean_stability < 0.35:
                mode = 'drifting'
            else:
                mode = 'associative'

            # ── Goal election ─────────────────────────────────────────────────
            if mean_tension > 0.5:
                goal = 'resolve_tension'
            elif coherence < 0.10:
                goal = 'expand_knowledge'
            elif coherence > 0.45 and mean_novelty < 0.3:
                goal = 'consolidate'
            elif entropy_norm > 0.70:
                goal = 'stabilise'
            else:
                goal = 'explore'

            # ── Scalar adjustment (exponential blend, α = 0.15) ───────────────
            α = 0.15

            # gain: reduce when saturated, increase when exploring
            target_gain = _DEFAULT_GAIN * (1.0 - pressure * 0.4 + entropy_norm * 0.2)
            self._gain = _clamp(
                self._gain * (1 - α) + target_gain * α,
                *_GAIN_BOUNDS
            )

            # decay: faster when saturated (clean up), slower when sparse
            target_decay = _DEFAULT_DECAY - (pressure - 0.5) * 0.004
            self._decay = _clamp(
                self._decay * (1 - α) + target_decay * α,
                *_DECAY_BOUNDS
            )

            # diffusion: stronger when exploring, weaker when focused
            target_diffusion = _DEFAULT_DIFFUSION * (0.5 + entropy_norm)
            self._diffusion = _clamp(
                self._diffusion * (1 - α) + target_diffusion * α,
                *_DIFFUSION_BOUNDS
            )

            self._mode     = mode
            self._goal     = goal
            self._entropy  = round(entropy_norm, 4)
            self._pressure = round(pressure, 4)

            self._save()
            self._write_cog_status()
            log.debug(
                'regulation tick %d: mode=%s goal=%s gain=%.3f decay=%.4f',
                self._tick_count, mode, goal, self._gain, self._decay
            )

        except Exception as e:
            log.warning('regulation tick error: %s', e)

    # ── State access ──────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            'mode':             self._mode,
            'goal':             self._goal,
            'gain_rate':        round(self._gain, 4),
            'decay_rate':       round(self._decay, 5),
            'diffusion_strength': round(self._diffusion, 4),
            'entropy':          self._entropy,
            'pressure':         self._pressure,
            'tick_count':       self._tick_count,
        }

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def goal(self) -> str:
        return self._goal

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        data = self.snapshot()
        data['updated_at'] = time.time()
        try:
            tmp = _REG_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, _REG_PATH)
        except Exception as e:
            log.debug('regulation save error: %s', e)

    def _write_cog_status(self) -> None:
        try:
            tmp = _COG_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'mode': self._mode, 'goal': self._goal}, f)
            os.replace(tmp, _COG_PATH)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            with open(_REG_PATH, encoding='utf-8') as f:
                d = json.load(f)
            self._gain      = float(d.get('gain_rate',          _DEFAULT_GAIN))
            self._decay     = float(d.get('decay_rate',         _DEFAULT_DECAY))
            self._diffusion = float(d.get('diffusion_strength', _DEFAULT_DIFFUSION))
            self._mode      = d.get('mode', 'exploring')
            self._goal      = d.get('goal', 'expand_knowledge')
            self._entropy   = float(d.get('entropy', 0.0))
            self._pressure  = float(d.get('pressure', 0.0))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.debug('regulation load error: %s', e)

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> 'Regulation':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
