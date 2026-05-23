"""
Node [M] — Meta-State
======================
Temporary concept activation layer. Implements lazy time-based decay:

    a(t) = a₀ × decay_rate ^ elapsed_minutes

No background thread — decay is computed on every read, not on a tick.
Survives crash and restart: the stored (value, stored_at) pair is enough
to reconstruct the current activation at any future time.

Implements IMetaState (protocols.py [0]).

Public API
----------
MetaState(path)            instantiate directly for DI / testing
  .reinforce(texts, gain)  boost activation for a list of concept texts
  .decay_to(now)           write-back all decayed values; prune dead entries
  .active(threshold)    →  dict[str, float]   concepts above threshold
  .top(n)               →  list[tuple[str,float]]  top-n by current activation
  .snapshot()           →  dict  JSON-safe — use for UI and cross-process reads

MetaState.get()            process singleton (lazy-created)

MOOD_META                  dict  {mood: (emoji, hex_colour, description)}

Config keys read from config.yaml → meta_state:
  decay_rate          float   0.97  per minute
  reinforce_gain      float   0.25  added per reinforce call
  active_threshold    float   0.20  floor for .active()
  max_concepts        int     500   hard cap, drops weakest
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from logger import get_logger

log = get_logger('meta_state')

_ROOT = os.path.join(os.path.dirname(__file__), '..')
_PATH = os.path.join(_ROOT, 'data', 'meta_state.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

# Defaults — overridden by config.yaml
_DECAY_RATE      = 0.97
_REINFORCE_GAIN  = 0.25
_ACTIVE_THRESHOLD = 0.20
_MAX_CONCEPTS    = 500

MOOD_META: dict[str, tuple[str, str, str]] = {
    'curious':    ('🔍', '#4FC3F7', 'Actively exploring new territory'),
    'conflicted': ('⚡', '#FF7043', 'Competing concepts held in tension'),
    'focused':    ('🎯', '#66BB6A', 'Deep attention on a tight concept cluster'),
    'drifting':   ('🌊', '#78909C', 'Low activation — semantic centre unfixed'),
    'saturated':  ('🌕', '#FFA726', 'Near stage capacity — growth slowing'),
    'exploring':  ('🧭', '#AB47BC', 'Steady mixed-state — no dominant pull'),
}


# ─────────────────────────────────────────────────────────────── Config ──

def _load_cfg() -> dict:
    try:
        import yaml
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('meta_state', {})
    except Exception:
        return {}


# ────────────────────────────────────────────────────────────── Helpers ──

def _now_dt() -> datetime:
    return datetime.now(tz=timezone.utc)

def _now_str() -> str:
    return _now_dt().isoformat(timespec='seconds')

def _parse_dt(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        # Bad/missing timestamp — return epoch so elapsed is huge and entry decays/prunes
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

def _elapsed_minutes(stored_at: str, now: datetime) -> float:
    return max((now - _parse_dt(stored_at)).total_seconds() / 60.0, 0.0)


# ─────────────────────────────────────────────────────────────── Engine ──

class MetaState:
    """
    Process singleton available via MetaState.get().
    Can also be instantiated directly for dependency injection.

    Internal storage:
        _entries = {
            "gravity": {"value": 0.85, "stored_at": "2026-05-23T10:00:00+00:00"},
            ...
        }
    The stored value is the activation AT stored_at.  Current activation is
    always computed lazily:  value × decay_rate ^ elapsed_minutes.
    """

    _instance: Optional[MetaState] = None

    def __init__(self, path: str = _PATH) -> None:
        cfg = _load_cfg()
        self._decay_rate       = float(cfg.get('decay_rate',       _DECAY_RATE))
        self._reinforce_gain   = float(cfg.get('reinforce_gain',   _REINFORCE_GAIN))
        self._active_threshold = float(cfg.get('active_threshold', _ACTIVE_THRESHOLD))
        self._max_concepts     = int(cfg.get('max_concepts',        _MAX_CONCEPTS))
        self._path             = path
        self._entries: dict[str, dict] = {}
        self._load()

    @classmethod
    def get(cls) -> MetaState:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── IMetaState protocol ──────────────────────────────────────────────

    def reinforce(self, texts: list[str], gain: float | None = None) -> None:
        """
        Boost activation for each text in the list.
        Reads current decayed value, adds gain, clamps to 1.0, stores with
        current timestamp so future lazy-decay is relative to this moment.
        """
        if not texts:
            return
        g   = gain if gain is not None else self._reinforce_gain
        now = _now_dt()
        for text in texts:
            if not text or not isinstance(text, str):
                continue
            current = self._decayed_value(text, now)
            new_val = max(0.0, min(current + g, 1.0))
            self._entries[text] = {'value': new_val, 'stored_at': now.isoformat(timespec='seconds')}
        self._enforce_cap(now)
        self._save()
        log.debug('reinforce: %d texts  gain=%.3f  pool=%d', len(texts), g, len(self._entries))

    def decay_to(self, now: datetime | None = None) -> None:
        """
        Write-back: compute all decayed values and store them with `now` as
        the new stored_at.  Prune entries that decayed below 0.001.
        Call this on each background tick to keep the JSON file fresh.
        """
        now = now or _now_dt()
        to_delete = []
        for text, entry in self._entries.items():
            v = self._decayed_value(text, now)
            if v < 0.001:
                to_delete.append(text)
            else:
                self._entries[text] = {'value': v, 'stored_at': now.isoformat(timespec='seconds')}
        for text in to_delete:
            del self._entries[text]
        if to_delete:
            log.debug('decay_to: pruned %d dead entries', len(to_delete))
        self._save()

    def active(self, threshold: float | None = None) -> dict[str, float]:
        """Return {text: activation} for all concepts above threshold."""
        floor  = threshold if threshold is not None else self._active_threshold
        now    = _now_dt()
        result = {}
        for text in self._entries:
            v = self._decayed_value(text, now)
            if v >= floor:
                result[text] = round(v, 4)
        return result

    def top(self, n: int = 10) -> list[tuple[str, float]]:
        """Return top-n (text, activation) sorted descending by current activation."""
        now = _now_dt()
        scored = [
            (text, round(self._decayed_value(text, now), 4))
            for text in self._entries
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    def snapshot(self) -> dict:
        """JSON-safe snapshot of current state for UI / persistence."""
        now      = _now_dt()
        active   = self.active()
        top_list = self.top(15)
        mood, intensity = self._mood(now)
        return {
            'timestamp':    now.isoformat(timespec='seconds'),
            'mood':         mood,
            'mood_intensity': round(intensity, 3),
            'active_count': len(active),
            'top':          [{'concept': t, 'activation': v} for t, v in top_list],
            'active':       active,
            'pool_size':    len(self._entries),
        }

    # ── Derived: mood ────────────────────────────────────────────────────

    def _mood(self, now: datetime) -> tuple[str, float]:
        """
        Rule-based mood from current activation distribution.
        Returns (mood_name, intensity 0-1).
        """
        act   = self.active()
        vals  = list(act.values())
        n     = len(vals)

        if n == 0:
            return 'drifting', 0.8

        import numpy as np
        avg = float(np.mean(vals))

        # read ambiguity spread from log
        score_log = os.path.join(_ROOT, 'logs', 'ambiguity_scores.jsonl')
        spread = 0.0
        if os.path.exists(score_log):
            try:
                lines = open(score_log, encoding='utf-8').readlines()
                sc = [json.loads(l)['score'] for l in lines[-100:]]
                if len(sc) > 1:
                    spread = float(np.std(sc))
            except Exception:
                pass

        if spread > 0.22:
            return 'conflicted', min(spread / 0.30, 1.0)
        if 2 <= n <= 8 and avg > 0.55:
            return 'focused', min(avg / 1.0, 1.0)
        if n >= 20:
            return 'curious', min(n / 50.0, 1.0)
        if n <= 1:
            return 'drifting', 0.7
        return 'exploring', 0.5

    # ── Internal ─────────────────────────────────────────────────────────

    def _decayed_value(self, text: str, now: datetime) -> float:
        entry = self._entries.get(text)
        if entry is None:
            return 0.0
        elapsed = _elapsed_minutes(entry['stored_at'], now)
        return entry['value'] * (self._decay_rate ** elapsed)

    def _enforce_cap(self, now: datetime) -> None:
        """Drop the weakest entries when pool exceeds max_concepts."""
        if len(self._entries) <= self._max_concepts:
            return
        scored = sorted(
            self._entries.items(),
            key=lambda kv: self._decayed_value(kv[0], now),
        )
        drop = len(self._entries) - self._max_concepts
        for text, _ in scored[:drop]:
            del self._entries[text]
        log.debug('cap enforced: dropped %d weakest entries', drop)

    def _save(self) -> None:
        import tempfile
        dir_ = os.path.dirname(self._path)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps({
            'entries':   self._entries,
            'saved_at':  _now_str(),
            'pool_size': len(self._entries),
        }, ensure_ascii=False, indent=2)
        # Atomic write: temp file → os.replace (crash-safe; never leaves partial JSON)
        with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False,
                                         suffix='.tmp', encoding='utf-8') as tf:
            tf.write(payload)
            tmp = tf.name
        os.replace(tmp, self._path)

    def _load(self) -> None:
        try:
            with open(self._path, encoding='utf-8') as f:
                d = json.load(f)
            self._entries = d.get('entries', {})
            log.debug('meta_state loaded: %d entries', len(self._entries))
        except FileNotFoundError:
            self._entries = {}
        except Exception as e:
            log.warning('meta_state load failed (%s) — starting fresh', e)
            self._entries = {}
