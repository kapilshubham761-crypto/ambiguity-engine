"""
Node [Wv] — Identity Persistence Layer (Worldview)
=====================================================
V4 Step 2: What survives. Not traits — history.

The system is intelligent turbulence without this. This module asks:
    What concepts persist across weeks?
    What contradictions remain unresolved?
    What goals survive entropy shifts?
    What regions become home territories?
    What abstractions become foundational?

The answers form a cognitive identity — a worldview — that biases
all future reasoning toward the engine's accumulated character.

Five dimensions of persistent identity:

    persistent_concepts
        Concepts still in semantic memory above a value threshold.
        These are what the engine has truly learned and retained.
        Not currently active — durably encoded.

    chronic_contradictions
        Unresolved contradictions older than 24 hours.
        These aren't failures. They're the engine's live tensions —
        the questions it cannot answer yet keeps returning to.

    surviving_goals
        Goals that have been selected across multiple different
        cognitive modes (focused, exploratory, reflective, etc).
        A goal that only fires in one mode is situational.
        A goal that fires across modes is identity.

    home_regions
        Regions with the highest cumulative visit count over time.
        The engine develops territorial familiarity — domains it
        knows deeply and returns to naturally.

    foundational_abstractions
        High-stability, high-reuse abstract concepts (stability > 0.7,
        reuse_frequency > 5). These are the load-bearing concepts
        around which other reasoning is organised.

Also computes:
    semantic_biases    — fraction of semantic memory per domain/region
    identity_summary   — one-paragraph natural language worldview description

Called from teacher.py check_in() — updates every 3 hours.
Also callable manually via worldview.py update().

Persists to data/worldview.json (atomic write).

Usage:
    from worldview import Worldview
    wv = Worldview.get()
    wv.update()
    portrait = wv.snapshot()
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import yaml

from logger import get_logger

log = get_logger('worldview')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_CFG_PATH  = os.path.join(_ROOT, 'config.yaml')
_DATA_PATH = os.path.join(_ROOT, 'data', 'worldview.json')

_SINGLETON: Optional['Worldview'] = None

# Thresholds
_CONCEPT_PERSIST_THRESHOLD = 0.05     # min semantic value to count as persistent
_CHRONIC_CONTRA_HOURS      = 24.0     # hours before a contradiction is "chronic"
_GOAL_SURVIVAL_MIN_MODES   = 1        # distinct modes a goal must fire in to "survive"
_HOME_REGION_MIN_VISITS    = 3        # min visits for a region to be "home"
_FOUNDATION_MIN_STABILITY  = 0.65
_FOUNDATION_MIN_REUSE      = 3


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Worldview                                                                    #
# --------------------------------------------------------------------------- #

class Worldview:
    """
    Longitudinal portrait of the engine's cognitive identity.
    Accumulates across sessions — each update() adds to history.
    """

    def __init__(self) -> None:
        # Persistent state loaded from disk
        self._region_visits: dict[str, int]          = {}   # region_id → cumulative visit count
        self._goal_mode_log: list[dict]               = []   # [{goal, mode, ts}]
        self._update_count: int                       = 0

        # Computed dimensions (refreshed on update())
        self._persistent_concepts: list[dict]         = []
        self._chronic_contradictions: list[dict]      = []
        self._surviving_goals: list[dict]             = []
        self._home_regions: list[dict]                = []
        self._foundational_abstractions: list[dict]   = []
        self._semantic_biases: dict[str, float]       = {}
        self._identity_summary: str                   = ''
        self._last_updated: str                       = ''

        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                raw = json.load(f)
            self._region_visits   = raw.get('region_visits', {})
            self._goal_mode_log   = raw.get('goal_mode_log', [])[-500:]
            self._update_count    = int(raw.get('update_count', 0))
            # Restore last computed dimensions
            self._persistent_concepts       = raw.get('persistent_concepts', [])
            self._chronic_contradictions    = raw.get('chronic_contradictions', [])
            self._surviving_goals           = raw.get('surviving_goals', [])
            self._home_regions              = raw.get('home_regions', [])
            self._foundational_abstractions = raw.get('foundational_abstractions', [])
            self._semantic_biases           = raw.get('semantic_biases', {})
            self._identity_summary          = raw.get('identity_summary', '')
            self._last_updated              = raw.get('last_updated', '')
        except Exception:
            pass

    def _save(self) -> None:
        _atomic_write(_DATA_PATH, {
            'region_visits':             self._region_visits,
            'goal_mode_log':             self._goal_mode_log[-500:],
            'update_count':              self._update_count,
            'persistent_concepts':       self._persistent_concepts,
            'chronic_contradictions':    self._chronic_contradictions,
            'surviving_goals':           self._surviving_goals,
            'home_regions':              self._home_regions,
            'foundational_abstractions': self._foundational_abstractions,
            'semantic_biases':           self._semantic_biases,
            'identity_summary':          self._identity_summary,
            'last_updated':              self._last_updated,
        })

    # ------------------------------------------------------------------ #
    # Event recording (called by teacher / ecology)                       #
    # ------------------------------------------------------------------ #

    def record_region_visit(self, region_id: str) -> None:
        """Increment visit count for a region. Call on every accept()."""
        if not region_id:
            return
        self._region_visits[region_id] = self._region_visits.get(region_id, 0) + 1

    def record_goal(self, goal: str, mode: str) -> None:
        """Log that a goal was active in a given cognitive mode."""
        self._goal_mode_log.append({
            'goal': goal,
            'mode': mode,
            'ts':   time.time(),
        })

    # ------------------------------------------------------------------ #
    # Dimension computation                                                #
    # ------------------------------------------------------------------ #

    def _compute_persistent_concepts(self) -> list[dict]:
        """Concepts durably encoded in the JAM field (high persistence)."""
        results = []
        try:
            from jam_field import JamField
            top = JamField.get().top(50, by='persistence')
            for text, props in top:
                val = props.get('persistence', 0.0)
                if val >= _CONCEPT_PERSIST_THRESHOLD:
                    results.append({
                        'concept':        text,
                        'semantic_value': round(val, 4),
                    })
        except Exception:
            pass
        return results

    def _compute_chronic_contradictions(self) -> list[dict]:
        """Contradictions that have been open longer than the chronic threshold."""
        results = []
        try:
            from contradiction import ContradictionRegistry
            unresolved = ContradictionRegistry.get().unresolved()
            now = time.time()
            for c in unresolved:
                created = c.get('created_at', now)
                if isinstance(created, str):
                    try:
                        dt = datetime.fromisoformat(created)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        created = dt.timestamp()
                    except Exception:
                        created = now
                age_hours = (now - created) / 3600.0
                if age_hours >= _CHRONIC_CONTRA_HOURS:
                    results.append({
                        'concept_a':   c.get('concept_a', ''),
                        'concept_b':   c.get('concept_b', ''),
                        'age_hours':   round(age_hours, 1),
                        'tension_a':   round(c.get('tension_a', 0.0), 3),
                        'tension_b':   round(c.get('tension_b', 0.0), 3),
                        'conflict_type': c.get('conflict_type', 'unknown'),
                    })
        except Exception:
            pass
        results.sort(key=lambda x: x['age_hours'], reverse=True)
        return results

    def _compute_surviving_goals(self) -> list[dict]:
        """Goals that have fired across multiple distinct cognitive modes."""
        goal_modes: dict[str, set] = defaultdict(set)
        goal_counts: dict[str, int] = defaultdict(int)
        for entry in self._goal_mode_log:
            g = entry.get('goal', '')
            m = entry.get('mode', '')
            if g and m:
                goal_modes[g].add(m)
                goal_counts[g] += 1

        results = []
        for goal, modes in goal_modes.items():
            if len(modes) >= _GOAL_SURVIVAL_MIN_MODES:
                results.append({
                    'goal':            goal,
                    'recurrence':      goal_counts[goal],
                    'distinct_modes':  sorted(modes),
                    'stability_score': round(len(modes) / 5.0, 3),  # 5 possible modes
                })
        results.sort(key=lambda x: x['stability_score'], reverse=True)
        return results

    def _compute_home_regions(self) -> list[dict]:
        """Regions with cumulative visit count above threshold."""
        total_visits = max(sum(self._region_visits.values()), 1)
        results = []
        for region_id, count in self._region_visits.items():
            if count >= _HOME_REGION_MIN_VISITS:
                results.append({
                    'region_id':     region_id,
                    'visit_count':   count,
                    'dominance_pct': round(count / total_visits * 100, 1),
                })
        results.sort(key=lambda x: x['visit_count'], reverse=True)
        return results[:10]

    def _compute_foundational_abstractions(self) -> list[dict]:
        """High-stability, high-reuse abstract concepts."""
        results = []
        try:
            from abstractor import Abstractor
            for a in Abstractor.get().all():
                if (a.get('stability', 0.0) >= _FOUNDATION_MIN_STABILITY and
                        a.get('reuse_frequency', 0) >= _FOUNDATION_MIN_REUSE):
                    results.append({
                        'name':             a['name'],
                        'stability':        round(a.get('stability', 0.0), 3),
                        'reuse_frequency':  a.get('reuse_frequency', 0),
                        'member_count':     len(a.get('members', [])),
                        'emergence_score':  round(a.get('emergence_score', 0.0), 3),
                    })
        except Exception:
            pass
        results.sort(key=lambda x: x['stability'] * x['reuse_frequency'], reverse=True)
        return results[:20]

    def _compute_semantic_biases(self) -> dict[str, float]:
        """
        Fraction of JAM field persistence devoted to each region's concepts.
        """
        biases: dict[str, float] = {}
        try:
            from jam_field import JamField
            from episodes import EpisodeStore
            top = JamField.get().top(500, by='persistence')
            total_val = sum(props.get('persistence', 0.0) for _, props in top)
            if total_val <= 0:
                return biases

            concept_regions: dict[str, str] = {}
            for ep in EpisodeStore.get().recent(500):
                region = ep.get('region') or 'unknown'
                for c in ep.get('concepts', []):
                    if c not in concept_regions:
                        concept_regions[c] = region

            region_value: dict[str, float] = defaultdict(float)
            for text, props in top:
                val = props.get('persistence', 0.0)
                r = concept_regions.get(text, 'unknown')
                region_value[r] += val

            for region, val in region_value.items():
                biases[region] = round(val / total_val, 4)
        except Exception:
            pass
        return dict(sorted(biases.items(), key=lambda x: x[1], reverse=True))

    # ------------------------------------------------------------------ #
    # Identity summary                                                     #
    # ------------------------------------------------------------------ #

    def _build_summary(self) -> str:
        parts = []

        if self._persistent_concepts:
            top3 = [c['concept'] for c in self._persistent_concepts[:3]]
            parts.append(f"Anchored in: {', '.join(top3)}")

        if self._chronic_contradictions:
            pair = self._chronic_contradictions[0]
            parts.append(
                f"In unresolved tension between '{pair['concept_a']}' and '{pair['concept_b']}' "
                f"({pair['age_hours']:.0f}h open)"
            )

        if self._surviving_goals:
            top_goal = self._surviving_goals[0]['goal'].replace('_', ' ')
            parts.append(f"Persistently driven to: {top_goal}")

        if self._home_regions:
            top_region = self._home_regions[0]
            parts.append(
                f"Home territory: '{top_region['region_id']}' "
                f"({top_region['dominance_pct']:.0f}% of visits)"
            )

        if self._foundational_abstractions:
            top_abs = self._foundational_abstractions[0]['name']
            parts.append(f"Reasoning scaffolded on: {top_abs}")

        return ' · '.join(parts) if parts else 'Identity forming — not enough history yet.'

    # ------------------------------------------------------------------ #
    # Main update                                                          #
    # ------------------------------------------------------------------ #

    def update(self) -> dict:
        """
        Recompute all five identity dimensions from current system state.
        Accumulates history — does not reset on each call.
        """
        # Record current goal + mode from regulation output
        try:
            import json, os as _os
            _cog = os.path.join(_os.path.dirname(__file__), '..', 'data', 'cog_status.json')
            with open(_cog, encoding='utf-8') as _f:
                _d = json.load(_f)
            self.record_goal(_d.get('goal', 'explore'), _d.get('mode', 'associative'))
        except Exception:
            pass

        # Record current active region
        try:
            from regions import RegionIndex
            ri = RegionIndex.get()
            if ri:
                region = ri.active_region()
                if region:
                    self.record_region_visit(str(region))
        except Exception:
            pass

        self._persistent_concepts       = self._compute_persistent_concepts()
        self._chronic_contradictions    = self._compute_chronic_contradictions()
        self._surviving_goals           = self._compute_surviving_goals()
        self._home_regions              = self._compute_home_regions()
        self._foundational_abstractions = self._compute_foundational_abstractions()
        self._semantic_biases           = self._compute_semantic_biases()
        self._identity_summary          = self._build_summary()
        self._last_updated              = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
        self._update_count             += 1

        self._save()
        log.info('worldview: updated (update #%d) — %d persistent, %d chronic, %d surviving goals, %d home regions, %d foundations',
                 self._update_count,
                 len(self._persistent_concepts),
                 len(self._chronic_contradictions),
                 len(self._surviving_goals),
                 len(self._home_regions),
                 len(self._foundational_abstractions))

        return self.snapshot()

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        return {
            'persistent_concepts':       self._persistent_concepts,
            'chronic_contradictions':    self._chronic_contradictions,
            'surviving_goals':           self._surviving_goals,
            'home_regions':              self._home_regions,
            'foundational_abstractions': self._foundational_abstractions,
            'semantic_biases':           self._semantic_biases,
            'identity_summary':          self._identity_summary,
            'last_updated':              self._last_updated,
            'update_count':              self._update_count,
        }

    def identity_summary(self) -> str:
        return self._identity_summary

    @classmethod
    def get(cls) -> 'Worldview':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
