"""
MetaRegulator — autonomous parameter regulation layer.

Monitors live system state and smoothly adjusts cognitive parameters
that were previously manual sliders. Writes to engine_config.json so
all modules pick up changes within one cycle via Config mtime cache.

Layer 1 — immutable physics  (never touched here)
    energy.total, memory limits, recursion caps

Layer 2 — adaptive regulators  (fully automated below)
    attention: novelty_strength, bias_strength
    meta_state: decay_rate, reinforce_gain, fatigue_k, cooling_alpha
    identity:   drift_rate
    goals:      all 5 drive weights

Layer 3 — personality (slow drift, handled by IdentityTracker/Evolver)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from logger import get_logger

log = get_logger('regulator')

_ROOT     = Path(__file__).parent.parent
_CFG_PATH = _ROOT / 'data' / 'engine_config.json'

# ── Hard bounds per variable ──────────────────────────────────────────────────

_BOUNDS: dict[str, tuple[float, float]] = {
    'attention.novelty_strength':  (0.00, 0.90),
    'attention.bias_strength':     (0.00, 0.80),
    'meta_state.decay_rate':       (0.92, 0.999),
    'meta_state.reinforce_gain':   (0.05, 0.70),
    'meta_state.fatigue_k':        (0.05, 0.85),
    'meta_state.cooling_alpha':    (0.10, 0.90),
    'identity.drift_rate':         (0.001, 0.08),
    'goals.reduce_uncertainty':    (0.05, 0.60),
    'goals.increase_novelty':      (0.05, 0.60),
    'goals.resolve_contradiction': (0.05, 0.60),
    'goals.maintain_stability':    (0.05, 0.60),
    'goals.expand_regions':        (0.03, 0.50),
}

_SMOOTH = 0.12   # exponential blend — slow, deliberate adaptation


def _clamp(val: float, key: str) -> float:
    lo, hi = _BOUNDS.get(key, (0.0, 1.0))
    return max(lo, min(hi, val))


def _blend(old: float, target: float, key: str) -> float:
    """Smooth transition: never jump, always drift."""
    return _clamp(old * (1.0 - _SMOOTH) + target * _SMOOTH, key)


# ── MetaRegulator ─────────────────────────────────────────────────────────────

class MetaRegulator:
    """Reads system metrics → computes targets → smoothly updates config."""

    _instance: 'MetaRegulator | None' = None

    @classmethod
    def get(cls) -> 'MetaRegulator':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._last_run = 0.0

    # ── State extraction ──────────────────────────────────────────────────────

    def _metrics(self) -> dict:
        """Read current cognitive state from all subsystems."""
        m = {
            'entropy':            0.5,   # [0,1] — 0=stagnant, 1=chaotic
            'contradiction_n':    0,
            'contradiction_rate': 0.0,   # open / total
            'novelty_sat':        0.5,   # mean exposure saturation [0,1]
            'loop_score':         0.0,   # fraction of top concepts seen >10×
            'energy':             0.8,   # EnergyBudget pool [0,1]
            'activation_spread':  0.5,   # std of meta-state activations
            'ambiguity_pressure': 0.5,   # mean tension score
        }

        try:
            sm = __import__('stability').StabilityMonitor.get()
            m['entropy'] = float(getattr(sm, '_entropy', 0.5))
        except Exception:
            pass

        try:
            cr = __import__('contradiction').ContradictionRegistry.get()
            reg = getattr(cr, '_registry', []) or []
            open_c = [c for c in reg if getattr(c, 'resolution_status', 'open') == 'open']
            m['contradiction_n']    = len(open_c)
            m['contradiction_rate'] = len(open_c) / max(1, len(reg))
        except Exception:
            pass

        try:
            nov = __import__('novelty').NoveltyTracker.get()
            exp = getattr(nov, '_exposures', {})
            if exp:
                counts = [
                    v.get('times_seen', 0) if isinstance(v, dict) else int(v or 0)
                    for v in list(exp.values())[:300]
                ]
                if counts:
                    mx = max(counts) or 1
                    m['novelty_sat']  = float(np.mean([c / mx for c in counts]))
                    m['loop_score']   = float(sum(1 for c in counts if c > 10) / len(counts))
        except Exception:
            pass

        try:
            eb = __import__('energy').EnergyBudget.get()
            m['energy'] = float(getattr(eb, '_pool', 0.8))
        except Exception:
            pass

        try:
            ms = __import__('meta_state').MetaState.get()
            entries = getattr(ms, '_entries', {})
            vals = [
                v.get('value', 0) if isinstance(v, dict) else float(v or 0)
                for v in list(entries.values())[:500]
            ]
            if vals:
                arr = np.array(vals, dtype=np.float32)
                m['activation_spread'] = float(min(arr.std() * 5.0, 1.0))
        except Exception:
            pass

        try:
            tt = __import__('tension').TensionTracker.get()
            store = getattr(tt, '_scores', {})
            if store:
                scores = [float(np.mean(v[-20:])) for v in store.values() if v]
                if scores:
                    m['ambiguity_pressure'] = float(np.mean(scores))
        except Exception:
            pass

        return m

    # ── Target computation ────────────────────────────────────────────────────

    def _compute_targets(self, m: dict) -> dict:
        entropy    = m['entropy']
        contra_r   = m['contradiction_rate']
        contra_n   = m['contradiction_n']
        nov_sat    = m['novelty_sat']
        energy     = m['energy']
        loop       = m['loop_score']
        spread     = m['activation_spread']
        ambiguity  = m['ambiguity_pressure']

        t: dict[str, float] = {}

        # ── novelty_strength ─────────────────────────────────────────────────
        # Push curiosity up when stagnant or looping; pull back when unstable
        push = (1.0 - entropy) * 0.55 + loop * 0.30
        pull = (1.0 - energy)  * 0.35 + contra_r * 0.20
        t['attention.novelty_strength'] = max(0.0, push - pull)

        # ── bias_strength (familiarity gravity) ──────────────────────────────
        # High when low-energy or volatile — seek safe ground
        t['attention.bias_strength'] = (
            (1.0 - energy) * 0.40 +
            contra_r       * 0.30 +
            (1.0 - spread) * 0.20
        )

        # ── decay_rate ───────────────────────────────────────────────────────
        # Fast decay (low value) when pool saturated → flush stale concepts
        # Slow decay when exploring fresh territory → preserve weak signals
        saturation = nov_sat * 0.6 + loop * 0.4
        t['meta_state.decay_rate'] = 0.920 + (1.0 - saturation) * 0.079

        # ── reinforce_gain ───────────────────────────────────────────────────
        # Strong reinforcement when high energy + low loops
        # Weak when obsessing or looping
        t['meta_state.reinforce_gain'] = max(0.05, (
            0.20 +
            energy    * 0.25 -
            loop      * 0.20 -
            contra_r  * 0.10
        ))

        # ── fatigue (repetition penalty) ─────────────────────────────────────
        # Anti-obsession pressure — rises sharply with loops
        t['meta_state.fatigue_k'] = min(0.85,
            0.08 + loop * 0.65 + nov_sat * 0.18
        )

        # ── cooling_alpha ────────────────────────────────────────────────────
        # Cognitive cooldown — high when energy low or activations spiking
        t['meta_state.cooling_alpha'] = min(0.90, (
            0.25 +
            (1.0 - energy) * 0.30 +
            contra_r       * 0.20 +
            (1.0 - spread) * 0.15
        ))

        # ── identity drift rate ──────────────────────────────────────────────
        # Stable normally; plastic under heavy contradiction + novelty exposure
        drift_pressure = contra_r * 0.04 + t['attention.novelty_strength'] * 0.025
        t['identity.drift_rate'] = 0.003 + drift_pressure

        # ── goal weights ─────────────────────────────────────────────────────
        # Each goal gets a base share + situational bonus; normalised to sum=1
        g_novelty    = 0.15 + (1.0 - entropy) * 0.20 + loop    * 0.10
        g_contra     = 0.08 + contra_r         * 0.30 + ambiguity * 0.10
        g_stability  = 0.10 + (1.0 - energy)  * 0.15 + (1.0 - spread) * 0.10
        g_uncertainty= 0.10 + ambiguity        * 0.20
        g_expand     = 0.08 + entropy          * 0.18

        total = g_novelty + g_contra + g_stability + g_uncertainty + g_expand
        scale = 1.0 / max(total, 1e-6)
        t['goals.increase_novelty']       = g_novelty    * scale
        t['goals.resolve_contradiction']  = g_contra     * scale
        t['goals.maintain_stability']     = g_stability  * scale
        t['goals.reduce_uncertainty']     = g_uncertainty * scale
        t['goals.expand_regions']         = g_expand     * scale

        return t

    # ── Apply to config ───────────────────────────────────────────────────────

    def _apply(self, targets: dict) -> None:
        try:
            cfg = json.loads(_CFG_PATH.read_text(encoding='utf-8'))
        except Exception:
            cfg = {}

        for key, target in targets.items():
            section, param = key.split('.', 1)
            old = float(cfg.get(section, {}).get(param, target))
            cfg.setdefault(section, {})[param] = round(_blend(old, target, key), 4)

        cfg['_regulated'] = True

        try:
            tmp = _CFG_PATH.with_suffix('.tmp')
            tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
            tmp.replace(_CFG_PATH)
            from config import Config
            Config.get_instance()._mtime = 0.0
        except Exception as e:
            log.debug('regulator write failed: %s', e)

    # ── Tick ─────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        m = self._metrics()
        t = self._compute_targets(m)
        self._apply(t)
        log.debug(
            'regulator | entropy=%.2f contra=%d(%.0f%%) sat=%.2f '
            'loop=%.2f energy=%.2f → novelty=%.2f bias=%.2f decay=%.3f',
            m['entropy'], m['contradiction_n'], m['contradiction_rate'] * 100,
            m['novelty_sat'], m['loop_score'], m['energy'],
            t['attention.novelty_strength'], t['attention.bias_strength'],
            t['meta_state.decay_rate'],
        )
