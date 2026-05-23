"""
Node [M] — Meta-State
======================
Temporary concept activation layer with V2 cognitive dynamics.

Decay formula (Phase A3 — activation cooling):
    a(t) = a₀ × decay_rate ^ (elapsed_minutes × (1 + alpha × a₀))

Reinforce formula (Phase A1 — saturation curve):
    activation += gain × (1 − activation)   [sigmoid mode]
    activation += gain                       [linear mode]

Fatigue (Phase A2): repeated hits inside window lose influence
    effective_gain = gain × 1/(1 + hits × fatigue_k)

Normalisation (Phase D1): when mean activation exceeds threshold,
    proportional or softmax rescaling keeps the distribution bounded.

Regional activation (Phase C): per-region sub-pools track spatial attention.
    Suppression applied to non-active regions during decay_to().
    Dormant regions stored and probabilistically revived.

No background thread — decay is computed on every read, not on a tick.
Survives crash and restart: (value, stored_at) pair fully encodes state.

Implements IMetaState (protocols.py [0]).

Public API
----------
MetaState(path)              instantiate directly for DI / testing
  .reinforce(texts, gain)    boost activation for a list of concept texts
  .decay_to(now)             write-back all decayed values; prune dead entries
  .active(threshold, region) →  dict[str, float]
  .top(n)                    →  list[tuple[str, float]]
  .snapshot()                →  dict  JSON-safe
  .set_region_index(ri)      wire in a RegionIndex for spatial attention

MetaState.get()              process singleton (lazy-created)

Config keys (config.yaml → meta_state):
  decay_rate              float  0.97   per minute
  reinforce_gain          float  0.25   base gain per reinforce
  active_threshold        float  0.20   floor for .active()
  max_concepts            int    500    hard cap
  gain_mode               str    sigmoid  "sigmoid" | "linear"
  fatigue_k               float  0.2    repeated-hit penalty
  fatigue_window_minutes  float  15     window for hit counting
  cooling_alpha           float  0.5    hot-concept burn multiplier
  normalisation_mode      str    proportional  "proportional" | "softmax"
  normalisation_threshold float  0.65   mean activation trigger
"""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
from datetime import datetime, timezone
from typing import Optional

from logger import get_logger

log = get_logger('meta_state')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_PATH     = os.path.join(_ROOT, 'data', 'meta_state.json')
_CFG_PATH = os.path.join(_ROOT, 'config.yaml')

_DECAY_RATE       = 0.97
_REINFORCE_GAIN   = 0.25
_ACTIVE_THRESHOLD = 0.20
_MAX_CONCEPTS     = 500

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

def _load_regions_cfg() -> dict:
    try:
        import yaml
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('regions', {})
    except Exception:
        return {}


# ────────────────────────────────────────────────────────────── Helpers ──

def _now_dt() -> datetime:
    return datetime.now(tz=timezone.utc)

def _now_str() -> str:
    return _now_dt().isoformat(timespec='seconds')

def _parse_dt(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

def _elapsed_minutes(stored_at: str, now: datetime) -> float:
    return max((now - _parse_dt(stored_at)).total_seconds() / 60.0, 0.0)


# ─────────────────────────────────────────────────────────────── Engine ──

class MetaState:
    """
    Process singleton available via MetaState.get().
    Can also be instantiated directly for dependency injection.

    Entry schema (flat pool):
        _entries = {
            "gravity": {
                "value": 0.85,
                "stored_at": "2026-05-23T10:00:00+00:00",
                "last_reinforced_at": "2026-05-23T10:00:00+00:00",  # A2
                "reinforce_count_window": 2,                          # A2
            },
            ...
        }

    Regional pool (Phase C):
        _regional = {
            region_id: { concept_text: entry_dict, ... },
            ...
        }
    """

    _instance: Optional['MetaState'] = None

    def __init__(self, path: str = _PATH) -> None:
        cfg = _load_cfg()
        self._decay_rate       = float(cfg.get('decay_rate',       _DECAY_RATE))
        self._reinforce_gain   = float(cfg.get('reinforce_gain',   _REINFORCE_GAIN))
        self._active_threshold = float(cfg.get('active_threshold', _ACTIVE_THRESHOLD))
        self._max_concepts     = int(cfg.get('max_concepts',       _MAX_CONCEPTS))
        # Phase A
        self._gain_mode        = str(cfg.get('gain_mode', 'sigmoid'))
        self._fatigue_k        = float(cfg.get('fatigue_k', 0.2))
        self._fatigue_window   = float(cfg.get('fatigue_window_minutes', 15.0))
        self._cooling_alpha    = float(cfg.get('cooling_alpha', 0.5))
        # Phase D1
        self._norm_mode        = str(cfg.get('normalisation_mode', 'proportional'))
        self._norm_threshold   = float(cfg.get('normalisation_threshold', 0.65))
        self._path             = path
        self._entries: dict[str, dict] = {}
        # Phase C — regional pools and dormancy
        self._regional: dict[str, dict[str, dict]] = {}
        self._active_region: Optional[str]          = None
        self._dormant: dict[str, dict]              = {}   # region_id: summary
        self._region_index = None                          # injected via set_region_index()
        self._load()

    @classmethod
    def get(cls) -> 'MetaState':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_region_index(self, ri) -> None:
        """Inject a RegionIndex for spatial attention (Phase C)."""
        self._region_index = ri

    # ── IMetaState protocol ──────────────────────────────────────────────

    def reinforce(self, texts: list[str], gain: float | None = None) -> None:
        """
        Boost activation for each text.
        A1: sigmoid saturation curve
        A2: fatigue multiplier within window
        Routes to regional pool if RegionIndex is wired (Phase C).
        """
        if not texts:
            return
        g   = gain if gain is not None else self._reinforce_gain
        now = _now_dt()
        now_str = now.isoformat(timespec='seconds')

        for text in texts:
            if not text or not isinstance(text, str):
                continue

            entry   = self._entries.get(text, {})
            current = self._decayed_value_from_entry(entry, now)

            # Phase A2 — fatigue multiplier
            effective_g = g * self._fatigue_multiplier(entry, now)

            # Phase A1 — gain mode
            if self._gain_mode == 'sigmoid':
                new_val = current + effective_g * (1.0 - current)
            else:
                new_val = current + effective_g
            new_val = max(0.0, min(new_val, 1.0))

            # Update fatigue tracking fields
            last_r  = entry.get('last_reinforced_at')
            hits    = entry.get('reinforce_count_window', 0)
            elapsed_since = _elapsed_minutes(last_r, now) if last_r else 999.0
            if elapsed_since > self._fatigue_window:
                hits = 0   # window expired — fresh start
            hits += 1

            self._entries[text] = {
                'value':                  new_val,
                'stored_at':              now_str,
                'last_reinforced_at':     now_str,
                'reinforce_count_window': hits,
            }

            # Phase C — route into regional pool too
            self._regional_reinforce(text, new_val, now_str)

        self._enforce_cap(now)
        self._save()
        log.debug('reinforce: %d texts  gain=%.3f  pool=%d', len(texts), g, len(self._entries))

    def decay_to(self, now: datetime | None = None) -> None:
        """
        Write-back all decayed values. Prune dead entries.
        Phase C: apply suppression to non-active region pools.
        Phase D1: normalise if mean activation exceeds threshold.
        """
        now = now or _now_dt()
        now_str = now.isoformat(timespec='seconds')

        # Flat pool decay
        to_delete = []
        for text, entry in self._entries.items():
            v = self._decayed_value_from_entry(entry, now)
            if v < 0.001:
                to_delete.append(text)
            else:
                entry['value']     = v
                entry['stored_at'] = now_str
        for text in to_delete:
            del self._entries[text]

        # Phase C — regional pool decay + suppression
        self._regional_decay(now, now_str)

        # Phase C — re-elect active region
        self._elect_active_region()

        # Phase C — probabilistic dormant revival
        self._maybe_revive_dormant(now_str)

        # Phase D1 — normalise if hot
        self._maybe_normalise()

        if to_delete:
            log.debug('decay_to: pruned %d flat entries', len(to_delete))
        self._save()

    def active(self, threshold: float | None = None,
               region: Optional[str] = None) -> dict[str, float]:
        """
        Return {text: activation} for concepts above threshold.
        Phase C: if region is given, query that region's sub-pool.
        """
        floor = threshold if threshold is not None else self._active_threshold
        now   = _now_dt()
        if region is not None:
            pool = self._regional.get(region, {})
            return {
                t: round(self._decayed_value_from_entry(e, now), 4)
                for t, e in pool.items()
                if self._decayed_value_from_entry(e, now) >= floor
            }
        return {
            t: round(self._decayed_value_from_entry(e, now), 4)
            for t, e in self._entries.items()
            if self._decayed_value_from_entry(e, now) >= floor
        }

    def top(self, n: int = 10) -> list[tuple[str, float]]:
        """Top-n (text, activation) sorted descending."""
        now = _now_dt()
        scored = [
            (t, round(self._decayed_value_from_entry(e, now), 4))
            for t, e in self._entries.items()
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    def snapshot(self) -> dict:
        """JSON-safe snapshot for UI / cross-process reads."""
        now      = _now_dt()
        active   = self.active()
        top_list = self.top(15)
        mood, intensity = self._mood(now)
        return {
            'timestamp':      now.isoformat(timespec='seconds'),
            'mood':           mood,
            'mood_intensity': round(intensity, 3),
            'active_count':   len(active),
            'top':            [{'concept': t, 'activation': v} for t, v in top_list],
            'active':         active,
            'pool_size':      len(self._entries),
            'active_region':  self._active_region,
            'region_count':   len(self._regional),
        }

    # ── Phase A helpers ──────────────────────────────────────────────────

    def _fatigue_multiplier(self, entry: dict, now: datetime) -> float:
        """A2: 1/(1 + hits*k) where hits resets after fatigue window."""
        if self._fatigue_k == 0.0:
            return 1.0
        last_r = entry.get('last_reinforced_at')
        if last_r is None:
            return 1.0
        elapsed = _elapsed_minutes(last_r, now)
        if elapsed > self._fatigue_window:
            return 1.0   # window expired — fresh start, no penalty
        hits = entry.get('reinforce_count_window', 0)
        return 1.0 / (1.0 + hits * self._fatigue_k)

    def _decayed_value_from_entry(self, entry: dict, now: datetime) -> float:
        """
        A3: activation cooling — hot concepts decay faster.
        a = a0 * r^(t * (1 + alpha * a0))
        At cooling_alpha=0 this is identical to v1.1.
        """
        if not entry:
            return 0.0
        a0      = entry.get('value', 0.0)
        elapsed = _elapsed_minutes(entry.get('stored_at', ''), now)
        exp     = elapsed * (1.0 + self._cooling_alpha * a0)
        return a0 * (self._decay_rate ** exp)

    def _decayed_value(self, text: str, now: datetime) -> float:
        return self._decayed_value_from_entry(self._entries.get(text, {}), now)

    # ── Phase C — regional pool ──────────────────────────────────────────

    def _regional_reinforce(self, text: str, value: float, now_str: str) -> None:
        """Write concept into its assigned regional sub-pool if RegionIndex is wired."""
        if self._region_index is None:
            return
        try:
            region_id = self._region_index.region_for(text)
        except Exception:
            return
        if region_id is None:
            return
        if region_id not in self._regional:
            self._regional[region_id] = {}
        self._regional[region_id][text] = {
            'value':      value,
            'stored_at':  now_str,
        }

    def _regional_decay(self, now: datetime, now_str: str) -> None:
        """Decay all regional pools; apply suppression to non-active regions."""
        rcfg = _load_regions_cfg()
        suppression = float(rcfg.get('suppression_factor', 0.90))
        bridge_thresh = float(rcfg.get('bridge_threshold', 0.30))

        for region_id, pool in list(self._regional.items()):
            is_active = (region_id == self._active_region)
            to_del = []
            for text, entry in pool.items():
                v = self._decayed_value_from_entry(entry, now)
                if v < 0.001:
                    to_del.append(text)
                    continue
                # Phase C3 — suppress non-active regions (bridge nodes exempt)
                if not is_active:
                    bridge_ok = False
                    if self._region_index is not None:
                        try:
                            bridge_ok = self._region_index.bridge_score(text) >= bridge_thresh
                        except Exception:
                            pass
                    if not bridge_ok:
                        v *= suppression
                    if v < 0.001:
                        to_del.append(text)
                        continue
                pool[text] = {'value': v, 'stored_at': now_str}
            for t in to_del:
                del pool[t]

    def _elect_active_region(self) -> None:
        """C2: elect the region with the highest mean activation as active."""
        if not self._regional:
            return
        now = _now_dt()
        best_region, best_score = None, -1.0
        for region_id, pool in self._regional.items():
            if not pool:
                continue
            vals = [self._decayed_value_from_entry(e, now) for e in pool.values()]
            top5 = sorted(vals, reverse=True)[:5]
            score = sum(top5) / max(len(top5), 1)
            if score > best_score:
                best_score = score
                best_region = region_id

        if best_region and best_region != self._active_region:
            log.debug('region switch: %s → %s (score=%.3f)',
                      self._active_region, best_region, best_score)
            self._store_dormant(self._active_region)
            self._active_region = best_region

    def _store_dormant(self, region_id: Optional[str]) -> None:
        """C5: snapshot a region into dormant storage when it loses active status."""
        if region_id is None or region_id not in self._regional:
            return
        pool = self._regional[region_id]
        now = _now_dt()
        top_concepts = sorted(
            pool.keys(),
            key=lambda t: self._decayed_value_from_entry(pool[t], now),
            reverse=True
        )[:10]
        # collect unresolved tensions for this region's concepts
        tensions = []
        try:
            from tension import TensionTracker
            tt = TensionTracker.get()
            tensions = [t for t in top_concepts if tt.is_hot(t)]
        except Exception:
            pass

        self._dormant[region_id] = {
            'top_concepts':        top_concepts,
            'peak_activation':     max(
                (self._decayed_value_from_entry(pool[t], now) for t in top_concepts),
                default=0.0
            ),
            'unresolved_tensions': tensions,
            'stored_at':           now.isoformat(timespec='seconds'),
        }

    def _maybe_revive_dormant(self, now_str: str) -> None:
        """C5: probabilistically revive a dormant region."""
        if not self._dormant:
            return
        rcfg = _load_regions_cfg()
        prob  = float(rcfg.get('latent_recall_prob', 0.02))
        floor = float(rcfg.get('min_revival_activation', 0.15))
        if random.random() >= prob:
            return
        # pick dormant region with highest historical peak
        best = max(self._dormant.items(), key=lambda kv: kv[1].get('peak_activation', 0))
        region_id, summary = best
        top_concepts = summary.get('top_concepts', [])
        if not top_concepts:
            return
        if region_id not in self._regional:
            self._regional[region_id] = {}
        for concept in top_concepts[:5]:
            pool_entry = self._regional[region_id].get(concept, {})
            current = self._decayed_value_from_entry(pool_entry, _now_dt())
            if current < floor:
                self._regional[region_id][concept] = {'value': floor, 'stored_at': now_str}
                # also boost flat pool
                flat = self._entries.get(concept, {})
                flat_v = self._decayed_value_from_entry(flat, _now_dt())
                if flat_v < floor:
                    self._entries[concept] = {'value': floor, 'stored_at': now_str,
                                              'last_reinforced_at': now_str,
                                              'reinforce_count_window': 0}
        log.debug('dormant region revived: %s  concepts=%s', region_id, top_concepts[:3])

    # ── Phase D1 — Normalisation ─────────────────────────────────────────

    def _maybe_normalise(self) -> None:
        """D1: rescale activation pool if mean exceeds normalisation_threshold."""
        if not self._entries:
            return
        vals = [e.get('value', 0.0) for e in self._entries.values()]
        mean = sum(vals) / len(vals)
        if mean <= self._norm_threshold:
            return

        now_str = _now_str()
        if self._norm_mode == 'softmax':
            exps   = {t: math.exp(e.get('value', 0.0)) for t, e in self._entries.items()}
            total  = sum(exps.values()) or 1.0
            scaled = {t: v / total for t, v in exps.items()}
        else:
            total  = sum(vals) or 1.0
            scaled = {t: e.get('value', 0.0) / total for t, e in self._entries.items()}

        for t, v in scaled.items():
            self._entries[t]['value']     = min(v, 1.0)
            self._entries[t]['stored_at'] = now_str
        log.debug('normalised pool: mean=%.3f  mode=%s', mean, self._norm_mode)

    # ── Derived: mood ────────────────────────────────────────────────────

    def _mood(self, now: datetime) -> tuple[str, float]:
        act  = self.active()
        vals = list(act.values())
        n    = len(vals)
        if n == 0:
            return 'drifting', 0.8
        import numpy as np
        avg = float(np.mean(vals))
        score_log = os.path.join(_ROOT, 'logs', 'ambiguity_scores.jsonl')
        spread = 0.0
        if os.path.exists(score_log):
            try:
                lines = open(score_log, encoding='utf-8').readlines()
                sc    = [json.loads(l)['score'] for l in lines[-100:]]
                if len(sc) > 1:
                    spread = float(np.std(sc))
            except Exception:
                pass
        if spread > 0.22:
            return 'conflicted', min(spread / 0.30, 1.0)
        if 2 <= n <= 8 and avg > 0.55:
            return 'focused', min(avg, 1.0)
        if n >= 20:
            return 'curious', min(n / 50.0, 1.0)
        if n <= 1:
            return 'drifting', 0.7
        return 'exploring', 0.5

    # ── Persistence ──────────────────────────────────────────────────────

    def _enforce_cap(self, now: datetime) -> None:
        if len(self._entries) <= self._max_concepts:
            return
        scored = sorted(
            self._entries.items(),
            key=lambda kv: self._decayed_value_from_entry(kv[1], now),
        )
        drop = len(self._entries) - self._max_concepts
        for text, _ in scored[:drop]:
            del self._entries[text]
        log.debug('cap enforced: dropped %d weakest entries', drop)

    def _save(self) -> None:
        dir_ = os.path.dirname(self._path)
        os.makedirs(dir_, exist_ok=True)
        payload = json.dumps({
            'entries':       self._entries,
            'regional':      self._regional,
            'active_region': self._active_region,
            'dormant':       self._dormant,
            'saved_at':      _now_str(),
            'pool_size':     len(self._entries),
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
            self._entries       = d.get('entries',       {})
            self._regional      = d.get('regional',      {})
            self._active_region = d.get('active_region', None)
            self._dormant       = d.get('dormant',       {})
            log.debug('meta_state loaded: %d flat + %d regions',
                      len(self._entries), len(self._regional))
        except FileNotFoundError:
            self._entries = {}
        except Exception as e:
            log.warning('meta_state load failed (%s) — starting fresh', e)
            self._entries = {}
