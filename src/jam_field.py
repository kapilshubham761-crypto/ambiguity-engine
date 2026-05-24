"""
JAM Field Core — Layer 2
=========================
Every concept exists as a living node with 10 field properties.
NOT fixed symbolic memory — an evolving field state.

Properties per node:
    activation   how strongly this concept is currently firing
    ambiguity    semantic uncertainty in local neighbourhood
    momentum     directional carry from previous activation state
    stability    consistency of activation over time (low drift = high stability)
    resonance    alignment with other active field nodes
    persistence  how established — grows with repeated exposure
    novelty      freshness — inverse of log exposure count
    tension      semantic distance from field centroid
    coherence    local cluster density
    drift        rate of activation change

Storage: jam_field table in graph.db (same SQLite file as graph).
In-memory dict for fast reads; flushed every FLUSH_EVERY updates.
Decay is applied lazily on read + eagerly on decay() call.

Public API
----------
JamField.get()                       → singleton
field.ingest(concepts, node_ids)     → update field from one article batch
field.update_dynamics(props)         → set ambiguity/resonance/tension/coherence
                                       from field_dynamics pass
field.decay()                        → time-decay all nodes, prune floor
field.top(n, by)                     → list[(text, props_dict)]
field.active(threshold)              → {text: activation}
field.snapshot()                     → summary dict + writes jam_field.json
field.flush()                        → force write to SQLite
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from typing import Optional

from logger import get_logger

log = get_logger('jam_field')

_ROOT     = os.path.join(os.path.dirname(__file__), '..')
_DB_PATH  = os.path.join(_ROOT, 'data', 'graph.db')
_SNAP_PATH = os.path.join(_ROOT, 'data', 'jam_field.json')

# ── Field constants ────────────────────────────────────────────────────────────
_GAIN        = 0.30    # activation gain per observation (sigmoid saturation)
_DECAY_RATE  = 0.9972  # per-minute activation decay (~0.85 after 60 min)
_MIN_ACTIVE  = 0.001   # prune below this
_FLUSH_EVERY = 100     # SQLite write batch size
_MAX_NODES   = 20_000  # in-memory cap; oldest pruned above this

_SINGLETON: Optional['JamField'] = None


# ── Node schema (in-memory) ────────────────────────────────────────────────────

def _zero_node(node_id: str, now: float) -> dict:
    return {
        'node_id':     node_id,
        'activation':  0.0,
        'ambiguity':   0.0,
        'momentum':    0.0,
        'stability':   0.5,
        'resonance':   0.0,
        'persistence': 0.0,
        'novelty':     1.0,
        'tension':     0.0,
        'coherence':   0.0,
        'drift':       0.0,
        'times_seen':  0,
        'first_seen':  now,
        'updated_at':  now,
    }


class JamField:
    """
    Unified field store for all 10 JAM node properties.
    Thread-safe. SQLite-backed with in-memory cache.
    """

    def __init__(self) -> None:
        self._con   = self._open_db()
        self._ensure_schema()
        self._nodes: dict[str, dict] = {}
        self._dirty: set[str]        = set()
        self._lock  = threading.Lock()
        self._update_count = 0
        self._load()

    # ── DB connection ──────────────────────────────────────────────────────────

    def _open_db(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        con = sqlite3.connect(_DB_PATH, check_same_thread=False)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=NORMAL')
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self) -> None:
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS jam_field (
                text        TEXT PRIMARY KEY,
                node_id     TEXT,
                activation  REAL DEFAULT 0.0,
                ambiguity   REAL DEFAULT 0.0,
                momentum    REAL DEFAULT 0.0,
                stability   REAL DEFAULT 0.5,
                resonance   REAL DEFAULT 0.0,
                persistence REAL DEFAULT 0.0,
                novelty     REAL DEFAULT 1.0,
                tension     REAL DEFAULT 0.0,
                coherence   REAL DEFAULT 0.0,
                drift       REAL DEFAULT 0.0,
                times_seen  INTEGER DEFAULT 1,
                first_seen  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
        """)
        self._con.commit()

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        rows = self._con.execute(
            'SELECT * FROM jam_field ORDER BY activation DESC LIMIT ?',
            (_MAX_NODES,)
        ).fetchall()
        for row in rows:
            self._nodes[row['text']] = {
                'node_id':    row['node_id'],
                'activation': row['activation'],
                'ambiguity':  row['ambiguity'],
                'momentum':   row['momentum'],
                'stability':  row['stability'],
                'resonance':  row['resonance'],
                'persistence': row['persistence'],
                'novelty':    row['novelty'],
                'tension':    row['tension'],
                'coherence':  row['coherence'],
                'drift':      row['drift'],
                'times_seen': row['times_seen'],
                'first_seen': row['first_seen'],
                'updated_at': row['updated_at'],
            }
        log.info('JAM field loaded: %d nodes', len(self._nodes))

    # ── Core: ingest ──────────────────────────────────────────────────────────

    def ingest(self, concepts: list, node_ids: list[str]) -> None:
        """
        Update field properties for a batch of concepts from one article.
        concepts  : list of Concept(text, embedding, source)
        node_ids  : parallel list of resolved graph node IDs
        """
        now = time.time()
        with self._lock:
            for concept, node_id in zip(concepts, node_ids):
                text = concept.text
                node = self._nodes.get(text)
                if node is None:
                    node = _zero_node(node_id, now)
                    self._nodes[text] = node

                # Lazy-decay current activation to now
                elapsed_min = (now - node['updated_at']) / 60.0
                prev_act = node['activation'] * (_DECAY_RATE ** max(elapsed_min, 0.0))

                # activation: sigmoid saturation gain
                new_act = prev_act + _GAIN * (1.0 - prev_act)

                # momentum: weighted carry
                momentum = prev_act * 0.65 + node['momentum'] * 0.35

                # drift: magnitude of activation change
                drift = abs(new_act - prev_act)

                # stability: EMA — low drift → higher stability
                stability = node['stability'] * 0.92 + (1.0 - min(drift * 8.0, 1.0)) * 0.08

                times_seen = node['times_seen'] + 1

                # novelty: inverse log — decreases with repetition
                novelty = 1.0 / (1.0 + math.log1p(times_seen))

                # persistence: saturating growth with exposure
                persistence = 1.0 - math.exp(-times_seen / 25.0)

                node['node_id']    = node_id
                node['activation'] = new_act
                node['momentum']   = momentum
                node['drift']      = drift
                node['stability']  = stability
                node['novelty']    = novelty
                node['persistence'] = persistence
                node['times_seen'] = times_seen
                node['updated_at'] = now

                self._dirty.add(text)

            self._update_count += len(concepts)
            if self._update_count >= _FLUSH_EVERY:
                self._flush_locked()
                self._update_count = 0

    # ── Dynamics write-back ───────────────────────────────────────────────────

    def update_dynamics(self, updates: dict[str, dict]) -> None:
        """
        Write ambiguity / resonance / tension / coherence computed by
        field_dynamics back into the field.
        updates: {text: {ambiguity, resonance, tension, coherence}}
        """
        with self._lock:
            for text, props in updates.items():
                node = self._nodes.get(text)
                if node is None:
                    continue
                for key in ('ambiguity', 'resonance', 'tension', 'coherence'):
                    if key in props:
                        node[key] = float(props[key])
                self._dirty.add(text)

    # ── Decay ─────────────────────────────────────────────────────────────────

    def decay(self) -> None:
        """Time-decay all nodes; prune below floor; write snapshot."""
        now = time.time()
        with self._lock:
            to_prune = []
            for text, node in self._nodes.items():
                elapsed_min = (now - node['updated_at']) / 60.0
                new_act = node['activation'] * (_DECAY_RATE ** max(elapsed_min, 0.0))
                if new_act < _MIN_ACTIVE:
                    to_prune.append(text)
                else:
                    node['activation'] = new_act
                    node['momentum']   = node['momentum'] * 0.80
                    node['updated_at'] = now
                    self._dirty.add(text)

            for text in to_prune:
                del self._nodes[text]

            self._flush_locked()

            if to_prune:
                placeholders = ','.join('?' * len(to_prune))
                try:
                    self._con.execute(
                        f'DELETE FROM jam_field WHERE text IN ({placeholders})',
                        to_prune
                    )
                    self._con.commit()
                except Exception as e:
                    log.warning('JAM prune error: %s', e)

            log.debug('JAM decay: pruned=%d active=%d', len(to_prune), len(self._nodes))

        self._write_snapshot()

    # ── Queries ───────────────────────────────────────────────────────────────

    def top(self, n: int = 20, by: str = 'activation') -> list[tuple[str, dict]]:
        """Top-n (text, props) sorted descending by property."""
        with self._lock:
            scored = sorted(
                self._nodes.items(),
                key=lambda x: x[1].get(by, 0.0),
                reverse=True
            )
        return scored[:n]

    def active(self, threshold: float = 0.05) -> dict[str, float]:
        """Return {text: live_activation} for nodes above threshold."""
        now = time.time()
        result = {}
        with self._lock:
            for text, node in self._nodes.items():
                elapsed_min = (now - node['updated_at']) / 60.0
                act = node['activation'] * (_DECAY_RATE ** max(elapsed_min, 0.0))
                if act >= threshold:
                    result[text] = round(act, 5)
        return result

    def node(self, text: str) -> dict | None:
        with self._lock:
            n = self._nodes.get(text)
            return dict(n) if n else None

    def all_texts(self) -> list[str]:
        with self._lock:
            return list(self._nodes.keys())

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        top5  = self.top(5)
        active = self.active(0.05)
        coh   = sum(v.get('activation', 0) for _, v in top5) / max(len(top5), 1)

        with self._lock:
            vals = [n['activation'] for n in self._nodes.values()]
            nov  = [n['novelty']    for n in self._nodes.values()]
            ten  = [n['tension']    for n in self._nodes.values()]
            stab = [n['stability']  for n in self._nodes.values()]

        n = max(len(vals), 1)
        snap = {
            'field_size':        len(self._nodes),
            'active_count':      len(active),
            'coherence':         round(coh, 6),
            'top':               [(t, round(v.get('activation', 0), 5)) for t, v in top5],
            'field_stats': {
                'mean_activation': round(sum(vals) / n, 6),
                'mean_novelty':    round(sum(nov)  / n, 4),
                'mean_tension':    round(sum(ten)  / n, 4),
                'mean_stability':  round(sum(stab) / n, 4),
            },
        }
        return snap

    def _write_snapshot(self) -> None:
        snap = self.snapshot()
        try:
            tmp = _SNAP_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(snap, f)
            os.replace(tmp, _SNAP_PATH)
        except Exception as e:
            log.debug('snapshot write error: %s', e)

    # ── Persistence ───────────────────────────────────────────────────────────

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._dirty:
            return
        rows = []
        for text in list(self._dirty):
            node = self._nodes.get(text)
            if node:
                rows.append((
                    text,
                    node.get('node_id'),
                    node['activation'], node['ambiguity'],  node['momentum'],
                    node['stability'],  node['resonance'],  node['persistence'],
                    node['novelty'],    node['tension'],    node['coherence'],
                    node['drift'],      node['times_seen'],
                    node['first_seen'], node['updated_at'],
                ))
        if not rows:
            self._dirty.clear()
            return
        try:
            with self._con:
                self._con.executemany("""
                    INSERT INTO jam_field (
                        text, node_id,
                        activation, ambiguity, momentum, stability, resonance,
                        persistence, novelty, tension, coherence, drift,
                        times_seen, first_seen, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(text) DO UPDATE SET
                        node_id     = excluded.node_id,
                        activation  = excluded.activation,
                        ambiguity   = excluded.ambiguity,
                        momentum    = excluded.momentum,
                        stability   = excluded.stability,
                        resonance   = excluded.resonance,
                        persistence = excluded.persistence,
                        novelty     = excluded.novelty,
                        tension     = excluded.tension,
                        coherence   = excluded.coherence,
                        drift       = excluded.drift,
                        times_seen  = excluded.times_seen,
                        updated_at  = excluded.updated_at
                """, rows)
            self._dirty.clear()
        except Exception as e:
            log.warning('JAM flush error: %s', e)

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> 'JamField':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
