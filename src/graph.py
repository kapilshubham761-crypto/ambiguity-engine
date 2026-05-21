"""
Phase 3: Semantic Graph Construction & Persistence.

Nodes  — concepts with embeddings, seen-timestamps, activation counts.
Edges  — weighted co-occurrence links that reinforce on repetition.
Store  — NetworkX in memory, SQLite on disk, daily JSON snapshots.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from datetime import date, datetime
from typing import Optional

import networkx as nx
import numpy as np

from logger import get_logger

log = get_logger('graph')

# --------------------------------------------------------------------------- #
# Paths (resolved relative to this file's location)                           #
# --------------------------------------------------------------------------- #

_ROOT       = os.path.join(os.path.dirname(__file__), '..')
_DB_PATH    = os.path.join(_ROOT, 'data', 'graph.db')
_SNAP_DIR   = os.path.join(_ROOT, 'snapshots')

# --------------------------------------------------------------------------- #
# Similarity                                                                   #
# --------------------------------------------------------------------------- #

def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom > 0 else 0.0


# --------------------------------------------------------------------------- #
# SQLite helpers                                                               #
# --------------------------------------------------------------------------- #

def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    # 3.1a — node schema
    con.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            id               TEXT PRIMARY KEY,
            text             TEXT NOT NULL UNIQUE,
            embedding        TEXT NOT NULL,
            first_seen       TEXT NOT NULL,
            last_seen        TEXT NOT NULL,
            activation_count INTEGER NOT NULL DEFAULT 1
        )
    """)
    # 3.1b — edge schema
    con.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            source       TEXT NOT NULL,
            target       TEXT NOT NULL,
            weight       REAL NOT NULL DEFAULT 1.0,
            edge_type    TEXT NOT NULL DEFAULT 'co-occurrence',
            last_updated TEXT NOT NULL,
            PRIMARY KEY (source, target)
        )
    """)
    con.commit()


# --------------------------------------------------------------------------- #
# SemanticGraph                                                                #
# --------------------------------------------------------------------------- #

class SemanticGraph:
    """
    In-memory NetworkX graph backed by SQLite.
    Load on construction, save explicitly after each run.
    """

    MERGE_THRESHOLD = 0.85   # 3.2a — string-equal or cosine >= this → merge
    EDGE_THRESHOLD  = 0.05   # 3.3b — co-occurring concepts always link; floor weight here
    REINFORCE       = 1.10   # edge weight multiplier on re-co-occurrence

    def __init__(self) -> None:
        self._g: nx.Graph = nx.Graph()
        self._con: sqlite3.Connection = _connect()
        _ensure_schema(self._con)
        self._load_from_db()
        log.info('Graph ready: %d nodes, %d edges', self.node_count, self.edge_count)

        # 6.5 — run decay/pruning at most once per calendar day
        from maintenance import run_maintenance
        run_maintenance(self)

    # ------------------------------------------------------------------ #
    # Public read properties                                               #
    # ------------------------------------------------------------------ #

    @property
    def node_count(self) -> int:
        return self._g.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._g.number_of_edges()

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._g.nodes.get(node_id)

    def get_neighbors(self, node_id: str, top_k: int = 8) -> list[dict]:
        """Return up to top_k neighbors sorted by edge weight descending."""
        if node_id not in self._g:
            return []
        neighbors = [
            {**self._g.nodes[n], 'node_id': n,
             'weight': self._g[node_id][n]['weight']}
            for n in self._g.neighbors(node_id)
        ]
        neighbors.sort(key=lambda x: x['weight'], reverse=True)
        return neighbors[:top_k]

    def all_nodes(self) -> list[dict]:
        return [{'node_id': nid, **data} for nid, data in self._g.nodes(data=True)]

    def all_edges(self) -> list[dict]:
        return [
            {'source': u, 'target': v, **data}
            for u, v, data in self._g.edges(data=True)
        ]

    # ------------------------------------------------------------------ #
    # Update: add concepts from one input run                              #
    # ------------------------------------------------------------------ #

    def update(self, concepts: list) -> list[str]:
        """
        Ingest a list of Concept namedtuples (from extractor).
        Returns the list of resolved node IDs (one per concept).
        """
        now = datetime.utcnow().isoformat(timespec='seconds')
        resolved_ids: list[str] = []

        for concept in concepts:
            node_id = self._resolve_or_create(concept, now)
            resolved_ids.append(node_id)

        # 3.3 — edges between all pairs of concepts in this input
        self._update_edges(resolved_ids, now)

        return resolved_ids

    # ------------------------------------------------------------------ #
    # Node resolution                                                      #
    # ------------------------------------------------------------------ #

    def _resolve_or_create(self, concept, now: str) -> str:
        # 3.2a — try string match first (fast path)
        for nid, data in self._g.nodes(data=True):
            if data['text'] == concept.text:
                self._touch_node(nid, now)
                return nid

        # 3.2a — embedding similarity match
        best_id, best_sim = None, 0.0
        for nid, data in self._g.nodes(data=True):
            sim = _cosine(data['embedding'], concept.embedding)
            if sim > best_sim:
                best_sim = sim
                best_id = nid

        if best_sim >= self.MERGE_THRESHOLD:
            # 3.2b — match found: reinforce the existing node
            self._touch_node(best_id, now)
            log.debug('Merged "%s" → node %s (sim=%.3f)', concept.text, best_id, best_sim)
            return best_id

        # 3.2c — no match: new node
        return self._create_node(concept, now)

    def _create_node(self, concept, now: str) -> str:
        node_id = str(uuid.uuid4())
        attrs = {
            'text':             concept.text,
            'embedding':        concept.embedding,
            'first_seen':       now,
            'last_seen':        now,
            'activation_count': 1,
        }
        self._g.add_node(node_id, **attrs)
        log.debug('New node: "%s" (%s)', concept.text, node_id)
        return node_id

    def _touch_node(self, node_id: str, now: str) -> None:
        # 3.2b
        self._g.nodes[node_id]['last_seen'] = now
        self._g.nodes[node_id]['activation_count'] += 1

    # ------------------------------------------------------------------ #
    # Edge update                                                          #
    # ------------------------------------------------------------------ #

    def _update_edges(self, node_ids: list[str], now: str) -> None:
        for i, a in enumerate(node_ids):
            for b in node_ids[i + 1:]:
                emb_a = self._g.nodes[a]['embedding']
                emb_b = self._g.nodes[b]['embedding']
                sim = _cosine(emb_a, emb_b)

                if sim < self.EDGE_THRESHOLD:
                    continue

                if self._g.has_edge(a, b):
                    # 3.3c — reinforce existing edge
                    old = self._g[a][b]['weight']
                    new = old * self.REINFORCE
                    self._g[a][b]['weight']       = new
                    self._g[a][b]['last_updated'] = now
                    log.debug('Reinforced edge %s–%s: %.3f → %.3f', a, b, old, new)
                else:
                    # 3.3a/b — new edge seeded at cosine similarity
                    self._g.add_edge(a, b,
                                     weight=sim,
                                     edge_type='co-occurrence',
                                     last_updated=now)
                    log.debug('New edge %s–%s (sim=%.3f)', a, b, sim)

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def save(self) -> None:
        """3.4b — persist graph to SQLite after every run."""
        now = datetime.utcnow().isoformat(timespec='seconds')
        with self._con:
            # nodes
            for nid, data in self._g.nodes(data=True):
                emb_json = json.dumps(data['embedding'])
                self._con.execute("""
                    INSERT INTO nodes (id, text, embedding, first_seen, last_seen, activation_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_seen        = excluded.last_seen,
                        activation_count = excluded.activation_count,
                        embedding        = excluded.embedding
                """, (nid, data['text'], emb_json,
                      data['first_seen'], data['last_seen'],
                      data['activation_count']))

            # edges
            for u, v, data in self._g.edges(data=True):
                self._con.execute("""
                    INSERT INTO edges (source, target, weight, edge_type, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source, target) DO UPDATE SET
                        weight       = excluded.weight,
                        last_updated = excluded.last_updated
                """, (u, v, data['weight'], data['edge_type'], data['last_updated']))

        log.info('Saved: %d nodes, %d edges → %s', self.node_count, self.edge_count, _DB_PATH)

    def snapshot(self) -> str:
        """3.4c — write a dated JSON snapshot to snapshots/."""
        os.makedirs(_SNAP_DIR, exist_ok=True)
        filename = os.path.join(_SNAP_DIR, f"{date.today().isoformat()}.json")
        payload = {
            'date':      date.today().isoformat(),
            'node_count': self.node_count,
            'edge_count': self.edge_count,
            'nodes': [
                {k: v for k, v in data.items() if k != 'embedding'}
                for _, data in self._g.nodes(data=True)
            ],
            'edges': [
                {'source': u, 'target': v, **data}
                for u, v, data in self._g.edges(data=True)
            ],
        }
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        log.info('Snapshot written: %s', filename)
        return filename

    # ------------------------------------------------------------------ #
    # Load from DB                                                         #
    # ------------------------------------------------------------------ #

    def _load_from_db(self) -> None:
        """3.4b — reconstruct in-memory graph from SQLite on startup."""
        cur = self._con.execute("SELECT * FROM nodes")
        for row in cur.fetchall():
            self._g.add_node(
                row['id'],
                text             = row['text'],
                embedding        = json.loads(row['embedding']),
                first_seen       = row['first_seen'],
                last_seen        = row['last_seen'],
                activation_count = row['activation_count'],
            )

        cur = self._con.execute("SELECT * FROM edges")
        for row in cur.fetchall():
            if row['source'] in self._g and row['target'] in self._g:
                self._g.add_edge(
                    row['source'], row['target'],
                    weight       = row['weight'],
                    edge_type    = row['edge_type'],
                    last_updated = row['last_updated'],
                )
        log.debug('Loaded from DB: %d nodes, %d edges', self.node_count, self.edge_count)
