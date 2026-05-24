"""
Node [En] — Cognitive Energy Budget
=====================================
V3 Step 17: The engine has a finite energy pool.  Expensive
operations cost energy; the pool replenishes each background tick.

Activities and costs:
    simulation_per_step  0.04
    exploration          0.06
    region_switch        0.05
    abstraction_run      0.10

spend(activity) returns True if the engine can afford it and deducts
the cost.  Returns False if the budget is too low — callers should
skip the operation.

Usage:
    from energy import EnergyBudget
    eb = EnergyBudget.get()
    if eb.spend('exploration'):
        # do exploration
    eb.replenish()  # called each background tick
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('energy')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'energy.json')

_SINGLETON: Optional['EnergyBudget'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('energy', {})
    except Exception:
        return {}


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


class EnergyBudget:
    """
    Finite cognitive energy pool with per-activity costs.
    """

    def __init__(self) -> None:
        cfg = _cfg()
        self._total           = float(cfg.get('total',            1.0))
        self._replenish_rate  = float(cfg.get('replenish_per_tick', 0.08))
        raw_costs = cfg.get('costs', {})
        self._costs: dict[str, float] = {
            'simulation_per_step': float(raw_costs.get('simulation_per_step', 0.04)),
            'exploration':         float(raw_costs.get('exploration',         0.06)),
            'region_switch':       float(raw_costs.get('region_switch',       0.05)),
            'abstraction_run':     float(raw_costs.get('abstraction_run',     0.10)),
        }

        self._current: float = self._total
        self._spend_count: int = 0
        self._denied_count: int = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            self._current      = float(raw.get('current', self._total))
            self._spend_count  = int(raw.get('spend_count', 0))
            self._denied_count = int(raw.get('denied_count', 0))
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'current':       round(self._current, 4),
            'spend_count':   self._spend_count,
            'denied_count':  self._denied_count,
            'ts':            time.time(),
        })

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def _live_cost(self, activity: str) -> float:
        """Read activity cost from live config, fall back to init value."""
        try:
            from config import Config
            key_map = {
                'simulation_per_step': 'cost_simulation',
                'exploration':         'cost_exploration',
                'region_switch':       'cost_region_switch',
                'abstraction_run':     'cost_abstraction',
            }
            v = Config.get_instance().get('energy', key_map.get(activity, ''))
            return float(v) if v is not None else self._costs.get(activity, 0.05)
        except Exception:
            return self._costs.get(activity, 0.05)

    def spend(self, activity: str, units: int = 1) -> bool:
        """
        Attempt to spend energy for activity.
        Returns True if affordable; deducts cost.  False = skip.
        """
        cost = self._live_cost(activity) * units
        if self._current < cost:
            self._denied_count += 1
            log.debug('energy: DENIED %s (have=%.3f need=%.3f)',
                      activity, self._current, cost)
            return False
        self._current  = max(0.0, self._current - cost)
        self._spend_count += 1
        log.debug('energy: spent %.3f for %s (remaining=%.3f)',
                  cost, activity, self._current)
        return True

    def replenish(self) -> None:
        """Called each background tick to restore energy."""
        try:
            from config import Config
            rate = Config.get_instance().get('energy', 'replenish_per_tick')
            rate = float(rate) if rate is not None else self._replenish_rate
        except Exception:
            rate = self._replenish_rate
        self._current = min(self._total, self._current + rate)
        self._save()

    def level(self) -> float:
        return round(self._current, 4)

    def level_pct(self) -> float:
        return round(self._current / self._total, 4) if self._total > 0 else 0.0

    def snapshot(self) -> dict:
        return {
            'current':       self.level(),
            'total':         self._total,
            'level_pct':     self.level_pct(),
            'spend_count':   self._spend_count,
            'denied_count':  self._denied_count,
            'costs':         self._costs,
        }

    @classmethod
    def get(cls) -> 'EnergyBudget':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
