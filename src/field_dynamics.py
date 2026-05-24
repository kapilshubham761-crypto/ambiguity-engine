"""
Field Dynamics — Layer 3
=========================
Runs after each article batch is ingested into the JAM field.
Computes the emergent properties that require global field context:

    activation propagation  — active nodes bleed activation to graph neighbours
    ambiguity diffusion     — local neighbourhood entropy
    resonance               — alignment with the active field centroid
    tension                 — semantic distance from field centroid
    coherence (per node)    — fraction of neighbours that are also active

None of these can be computed inside ingest() because they need the
full field state and the graph topology simultaneously.

Public API
----------
FieldDynamics.get()                    → singleton
dynamics.propagate(field, graph)       → run one full dynamics pass
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import numpy as np

from logger import get_logger

if TYPE_CHECKING:
    from jam_field import JamField
    from graph import SemanticGraph

log = get_logger('field_dynamics')

# ── Constants ─────────────────────────────────────────────────────────────────
_DIFFUSION      = 0.12   # fraction of activation that bleeds to neighbours
_ACT_THRESHOLD  = 0.05   # minimum activation to participate in propagation
_TOP_ACTIVE     = 200    # max nodes considered "active" for centroid / resonance
_PROPAGATE_K    = 6      # top-k neighbours to propagate to per node

_SINGLETON = None


class FieldDynamics:

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ── Main entry ────────────────────────────────────────────────────────────

    def propagate(self, field: 'JamField', graph: 'SemanticGraph') -> None:
        """
        One full dynamics pass. Reads field + graph; writes back via
        field.update_dynamics(). Non-blocking — skips if already running.
        """
        if not self._lock.acquire(blocking=False):
            return
        try:
            t0 = time.perf_counter()
            active = field.active(_ACT_THRESHOLD)
            if not active:
                return

            # Pull active node data from graph
            node_data = self._fetch_node_data(active, field, graph)
            if not node_data:
                return

            # Compute field centroid (activation-weighted mean embedding)
            centroid = self._compute_centroid(node_data, active)

            # Compute per-node dynamics
            updates: dict[str, dict] = {}

            for text, (node_id, emb, act) in node_data.items():
                # tension: cosine distance from centroid
                tension = self._cosine_dist(emb, centroid) if centroid is not None else 0.0

                # resonance: cosine similarity to centroid
                resonance = 1.0 - tension

                # ambiguity: std of neighbour activations
                ambiguity = self._neighbour_entropy(node_id, active, graph)

                # coherence: fraction of graph neighbours that are active
                coherence = self._local_coherence(node_id, active, graph)

                updates[text] = {
                    'tension':   round(tension,   4),
                    'resonance': round(resonance, 4),
                    'ambiguity': round(ambiguity, 4),
                    'coherence': round(coherence, 4),
                }

            # Activation propagation — bleeds into neighbours
            propagation = self._compute_propagation(node_data, active, graph)
            if propagation:
                # Ingest propagated activations as a lightweight update
                self._apply_propagation(propagation, field)

            field.update_dynamics(updates)
            log.debug('dynamics pass: %d nodes, %.2fs', len(active), time.perf_counter() - t0)

        except Exception as e:
            log.warning('dynamics error: %s', e)
        finally:
            self._lock.release()

    # ── Node data fetch ───────────────────────────────────────────────────────

    def _fetch_node_data(
        self,
        active: dict[str, float],
        field: 'JamField',
        graph: 'SemanticGraph',
    ) -> dict[str, tuple]:
        """
        Returns {text: (node_id, embedding_array, activation)} for
        active nodes that exist in the graph with embeddings.
        """
        result = {}
        top_texts = sorted(active, key=active.get, reverse=True)[:_TOP_ACTIVE]
        g = graph._g

        # Build reverse lookup: node_id → text (from field)
        text_to_nid = {}
        for text in top_texts:
            node = field.node(text)
            if node and node.get('node_id'):
                text_to_nid[text] = node['node_id']

        for text, nid in text_to_nid.items():
            if nid not in g:
                continue
            emb = g.nodes[nid].get('embedding')
            if emb is None:
                continue
            result[text] = (nid, np.array(emb, dtype=np.float32), active[text])

        return result

    # ── Centroid ──────────────────────────────────────────────────────────────

    def _compute_centroid(
        self,
        node_data: dict,
        active: dict[str, float],
    ) -> np.ndarray | None:
        if not node_data:
            return None
        embs   = np.stack([v[1] for v in node_data.values()], axis=0)
        weights = np.array([active.get(t, 0.0) for t in node_data], dtype=np.float32)
        weights /= (weights.sum() + 1e-8)
        centroid = (embs * weights[:, None]).sum(axis=0)
        n = np.linalg.norm(centroid)
        return centroid / n if n > 0 else None

    # ── Per-node metrics ──────────────────────────────────────────────────────

    def _cosine_dist(self, emb: np.ndarray, centroid: np.ndarray) -> float:
        n_e = np.linalg.norm(emb)
        n_c = np.linalg.norm(centroid)
        if n_e == 0 or n_c == 0:
            return 0.0
        sim = float(np.dot(emb, centroid) / (n_e * n_c))
        return round(max(0.0, 1.0 - sim), 5)

    def _neighbour_entropy(
        self,
        node_id: str,
        active: dict[str, float],
        graph: 'SemanticGraph',
    ) -> float:
        """Std-dev of activation values among graph neighbours."""
        g = graph._g
        if node_id not in g:
            return 0.0
        # Build reverse nid→text for active nodes
        nid_to_text = {
            field_node.get('node_id'): t
            for t, _ in active.items()
            if True  # placeholder; real lookup below
        }
        neighbour_acts = []
        for nbr in g.neighbors(node_id):
            # Find if this neighbour is active
            nbr_text = graph._g.nodes[nbr].get('text', '')
            if nbr_text in active:
                neighbour_acts.append(active[nbr_text])
        if len(neighbour_acts) < 2:
            return 0.0
        arr = np.array(neighbour_acts, dtype=np.float32)
        return float(np.std(arr))

    def _local_coherence(
        self,
        node_id: str,
        active: dict[str, float],
        graph: 'SemanticGraph',
    ) -> float:
        """Fraction of immediate graph neighbours that are currently active."""
        g = graph._g
        if node_id not in g:
            return 0.0
        neighbours = list(g.neighbors(node_id))
        if not neighbours:
            return 0.0
        active_count = sum(
            1 for nbr in neighbours
            if g.nodes[nbr].get('text', '') in active
        )
        return active_count / len(neighbours)

    # ── Activation propagation ────────────────────────────────────────────────

    def _compute_propagation(
        self,
        node_data: dict,
        active: dict[str, float],
        graph: 'SemanticGraph',
    ) -> dict[str, float]:
        """
        For each active node, bleed _DIFFUSION * activation * edge_weight
        to top-k neighbours. Returns {neighbour_node_id: delta_activation}.
        """
        g = graph._g
        deltas: dict[str, float] = {}
        for text, (node_id, emb, act) in node_data.items():
            if act < _ACT_THRESHOLD or node_id not in g:
                continue
            neighbours = sorted(
                g.edges(node_id, data=True),
                key=lambda e: e[2].get('weight', 0),
                reverse=True,
            )[:_PROPAGATE_K]
            for _, nbr_id, edge_data in neighbours:
                w     = edge_data.get('weight', 0.1)
                delta = _DIFFUSION * act * min(w, 1.0)
                deltas[nbr_id] = deltas.get(nbr_id, 0.0) + delta
        return deltas

    def _apply_propagation(
        self,
        deltas: dict[str, float],
        field: 'JamField',
    ) -> None:
        """
        Apply propagated activation deltas directly to field nodes.
        Only touches nodes that already exist in the field.
        """
        now = time.time()
        with field._lock:
            # Build node_id → text reverse map
            nid_to_text = {
                node['node_id']: text
                for text, node in field._nodes.items()
                if node.get('node_id')
            }
            for nid, delta in deltas.items():
                text = nid_to_text.get(nid)
                if text is None:
                    continue
                node = field._nodes[text]
                node['activation'] = min(1.0, node['activation'] + delta * 0.3)
                node['updated_at'] = now
                field._dirty.add(text)

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> 'FieldDynamics':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
