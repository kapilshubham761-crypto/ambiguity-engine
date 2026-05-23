"""
Node [Mem] — Temporal Memory
==============================
V3 Step 1: Three-layer memory architecture separating timescales.

Layer     Decay (per min)  Role
───────   ───────────────  ──────────────────────────────────────────
working   0.97             fast, recency-driven  (mirrors meta_state)
episodic  0.9997           episode-anchored, ~4h half-life
semantic  0.99997          consolidated knowledge, ~7-day half-life

Promotion flow:
  reinforce(concept) → always writes to working
  working value > promote_threshold → also writes to episodic
  episodic hit count ≥ promote_count  → also writes to semantic

Decay formula (same as meta_state [M]):
  a(t) = a₀ × rate^elapsed_minutes

value(concept)  →  weighted aggregate across all three layers
  weights: working=0.50  episodic=0.35  semantic=0.15

Persists to data/memory.json (atomic write).
Singleton via TemporalMemory.get().
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('memory')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_PATH     = os.path.join(_ROOT, 'data', 'memory.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_LAYER_WEIGHTS = {'working': 0.50, 'episodic': 0.35, 'semantic': 0.15}

_SINGLETON: Optional['TemporalMemory'] = None


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('temporal_memory', {})
    except Exception:
        return {}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _now_str() -> str:
    return _now().isoformat(timespec='seconds')


def _parse_dt(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# TemporalMemory                                                               #
# --------------------------------------------------------------------------- #

class TemporalMemory:
    """
    Three-layer temporal memory with per-layer decay and promotion.

    Internal storage per layer:
        { concept: { value: float, stored_at: ISO, count: int } }
    """

    def __init__(self, path: str = _PATH) -> None:
        self._path = path
        cfg = _cfg()
        w = cfg.get('working',  {})
        e = cfg.get('episodic', {})
        s = cfg.get('semantic', {})

        self._w_rate   = float(w.get('decay_rate', 0.97))
        self._w_gain   = float(w.get('gain', 0.40))
        self._w_thresh = float(w.get('promote_threshold', 0.35))

        self._e_rate   = float(e.get('decay_rate', 0.9997))
        self._e_gain   = float(e.get('gain', 0.10))
        self._e_pcount = int(e.get('promote_count', 3))
        self._e_thresh = float(e.get('promote_threshold', 0.15))

        self._s_rate   = float(s.get('decay_rate', 0.99997))
        self._s_gain   = float(s.get('gain', 0.03))

        self._working:  dict[str, dict] = {}
        self._episodic: dict[str, dict] = {}
        self._semantic: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def reinforce(self, concepts: list[str], gain_override: float | None = None) -> None:
        """
        Reinforce a list of concept texts.
        Always updates working; promotes to episodic/semantic when thresholds met.
        """
        now = _now()
        now_s = now.isoformat(timespec='seconds')

        for text in concepts:
            if not text:
                continue
            # Working layer
            w_val = self._read_layer(self._working, text, now, self._w_rate)
            g = gain_override if gain_override is not None else self._w_gain
            new_w = min(1.0, w_val + g * (1.0 - w_val))
            self._write_layer(self._working, text, new_w, now_s)

            # Promote to episodic if working threshold crossed
            if new_w >= self._w_thresh:
                e_val = self._read_layer(self._episodic, text, now, self._e_rate)
                new_e = min(1.0, e_val + self._e_gain * (1.0 - e_val))
                cnt   = self._episodic.get(text, {}).get('count', 0) + 1
                self._write_layer(self._episodic, text, new_e, now_s, count=cnt)

                # Promote to semantic after enough episodic hits
                if cnt >= self._e_pcount and new_e >= self._e_thresh:
                    s_val  = self._read_layer(self._semantic, text, now, self._s_rate)
                    new_s  = min(1.0, s_val + self._s_gain * (1.0 - s_val))
                    s_cnt  = self._semantic.get(text, {}).get('count', 0) + 1
                    self._write_layer(self._semantic, text, new_s, now_s, count=s_cnt)

        self._save()

    def value(self, concept: str) -> float:
        """Weighted aggregate across all three layers (working+episodic+semantic)."""
        now = _now()
        w = self._read_layer(self._working,  concept, now, self._w_rate)
        e = self._read_layer(self._episodic, concept, now, self._e_rate)
        s = self._read_layer(self._semantic, concept, now, self._s_rate)
        return round(
            _LAYER_WEIGHTS['working']  * w +
            _LAYER_WEIGHTS['episodic'] * e +
            _LAYER_WEIGHTS['semantic'] * s,
            4
        )

    def working_value(self, concept: str) -> float:
        return self._read_layer(self._working, concept, _now(), self._w_rate)

    def episodic_value(self, concept: str) -> float:
        return self._read_layer(self._episodic, concept, _now(), self._e_rate)

    def semantic_value(self, concept: str) -> float:
        return self._read_layer(self._semantic, concept, _now(), self._s_rate)

    def decay_to(self, now: datetime | None = None) -> None:
        """Prune dead entries across all layers (< 0.001 after decay)."""
        t = now or _now()
        for layer, rate in [
            (self._working,  self._w_rate),
            (self._episodic, self._e_rate),
            (self._semantic, self._s_rate),
        ]:
            dead = [k for k in list(layer)
                    if self._read_layer(layer, k, t, rate) < 0.001]
            for k in dead:
                del layer[k]
        self._save()

    def top_semantic(self, n: int = 20) -> list[tuple[str, float]]:
        """Top-n concepts by semantic layer value (deepest knowledge)."""
        now = _now()
        scored = [
            (k, self._read_layer(self._semantic, k, now, self._s_rate))
            for k in self._semantic
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    def top_working(self, n: int = 20) -> list[tuple[str, float]]:
        """Top-n concepts by working layer value (most recent attention)."""
        now = _now()
        scored = [
            (k, self._read_layer(self._working, k, now, self._w_rate))
            for k in self._working
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    def in_semantic(self, concept: str, threshold: float = 0.01) -> bool:
        return self.semantic_value(concept) >= threshold

    def snapshot(self) -> dict:
        now = _now()
        return {
            'working_count':  len(self._working),
            'episodic_count': len(self._episodic),
            'semantic_count': len(self._semantic),
            'top_semantic':   self.top_semantic(10),
            'top_working':    self.top_working(10),
        }

    @classmethod
    def get(cls) -> 'TemporalMemory':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _read_layer(layer: dict, concept: str, now: datetime, rate: float) -> float:
        entry = layer.get(concept)
        if entry is None:
            return 0.0
        a0      = float(entry.get('value', 0.0))
        stored  = _parse_dt(entry.get('stored_at', '1970-01-01T00:00:00+00:00'))
        elapsed = max(0.0, (now - stored).total_seconds() / 60.0)
        return float(a0 * (rate ** elapsed))

    @staticmethod
    def _write_layer(layer: dict, concept: str, value: float,
                     now_str: str, count: int | None = None) -> None:
        entry = layer.setdefault(concept, {})
        entry['value']     = round(value, 6)
        entry['stored_at'] = now_str
        if count is not None:
            entry['count'] = count
        elif 'count' not in entry:
            entry['count'] = 0

    def _save(self) -> None:
        dir_ = os.path.dirname(self._path)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps({
            'working':  self._working,
            'episodic': self._episodic,
            'semantic': self._semantic,
        }, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, self._path)

    def _load(self) -> None:
        try:
            with open(self._path, encoding='utf-8') as f:
                d = json.load(f)
            self._working  = d.get('working',  {})
            self._episodic = d.get('episodic', {})
            self._semantic = d.get('semantic', {})
            log.debug('memory loaded: w=%d e=%d s=%d',
                      len(self._working), len(self._episodic), len(self._semantic))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning('memory load error: %s', e)
