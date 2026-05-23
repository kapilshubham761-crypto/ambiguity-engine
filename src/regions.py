"""
Node [R] — Region Index
========================
Phase C1: Real graph partitioning via NetworkX greedy modularity communities.
Assigns every concept node to a region (community). Provides:
  - region_for(concept_text) → region_id | None
  - bridge_score(concept_text) → float   (Phase C4)
  - assign()  — re-partition graph (call after graph growth)
  - active_region() → str | None
  - nodes_in(region) → list[node_id]

Bridge nodes (Phase C4):
  Concepts whose concept text appears in ≥ 2 regions (because the same text
  can land in different communities across assign() calls, or because they
  connect dense subgraphs). bridge_score = 1 / log(min_size_region + e).
  Approximate method: look at ego-graph neighbourhood and count distinct
  community memberships of immediate neighbours.

Falls back to whole-graph mode (all nodes in one region) when:
  - graph has fewer than min_graph_size nodes (config: regions.min_graph_size)
  - community detection fails for any reason

Implements IRegions (protocols.py [0]).
"""

from __future__ import annotations

import math
import os
from typing import Optional

import yaml

from logger import get_logger
from protocols import IRegions

log = get_logger('regions')

_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return yaml.safe_load(f).get('regions', {})
    except Exception:
        return {}


class RegionIndex:
    """
    Real region index using greedy modularity communities.
    Degrades gracefully to whole-graph mode on small graphs.
    """

    def __init__(self, graph=None) -> None:
        self._graph = graph
        cfg = _cfg()
        self._min_graph_size = int(cfg.get('min_graph_size', 200))
        self._bridge_threshold = float(cfg.get('bridge_threshold', 0.30))
        # node_id → region_id (str, e.g. "region_0")
        self._node_region: dict[str, str] = {}
        # concept text → region_id
        self._text_region:  dict[str, str] = {}
        # concept text → bridge_score float
        self._bridge_scores: dict[str, float] = {}
        # region_id → set of node_ids
        self._communities:   dict[str, set]   = {}
        self._active_region: Optional[str]     = None

        if graph is not None:
            self.assign()

    # ------------------------------------------------------------------ #
    # IRegions interface                                                   #
    # ------------------------------------------------------------------ #

    def assign(self) -> None:
        """
        Partition the graph into communities using greedy modularity.
        Skips if graph is too small — falls back to whole-graph mode.
        """
        if self._graph is None:
            return
        g = self._graph._g
        n = g.number_of_nodes()
        if n < self._min_graph_size:
            log.debug('regions: graph too small (%d < %d) — whole-graph mode',
                      n, self._min_graph_size)
            self._node_region   = {}
            self._text_region   = {}
            self._bridge_scores = {}
            self._communities   = {}
            return

        try:
            from networkx.algorithms.community import greedy_modularity_communities
            communities = list(greedy_modularity_communities(g.to_undirected()))
        except Exception as e:
            log.warning('regions: community detection failed (%s) — whole-graph mode', e)
            return

        self._node_region  = {}
        self._text_region  = {}
        self._communities  = {}

        for idx, community in enumerate(communities):
            region_id = f'region_{idx}'
            self._communities[region_id] = set(community)
            for node_id in community:
                self._node_region[node_id] = region_id
                text = g.nodes[node_id].get('text', '')
                if text:
                    self._text_region[text] = region_id

        log.debug('regions: %d communities from %d nodes', len(communities), n)
        self._compute_bridge_scores()

    def active_region(self) -> Optional[str]:
        return self._active_region

    def nodes_in(self, region: object) -> list:
        """Return node IDs in a region. None → all nodes."""
        if region is None or not self._communities:
            if self._graph is None:
                return []
            return list(self._graph._g.nodes())
        return list(self._communities.get(str(region), set()))

    # ------------------------------------------------------------------ #
    # Phase C extensions                                                   #
    # ------------------------------------------------------------------ #

    def region_for(self, concept_text: str) -> Optional[str]:
        """Return the region_id for a concept text, or None in whole-graph mode."""
        return self._text_region.get(concept_text)

    def bridge_score(self, concept_text: str) -> float:
        """
        Phase C4: fraction of a concept's graph neighbours that belong to
        different regions. Range [0, 1]. 0 = no bridging. 1 = all neighbours
        are in different regions.
        """
        return self._bridge_scores.get(concept_text, 0.0)

    def bridge_nodes(self, threshold: Optional[float] = None) -> list[str]:
        """Return concept texts with bridge_score above threshold."""
        t = threshold if threshold is not None else self._bridge_threshold
        return [text for text, score in self._bridge_scores.items() if score >= t]

    def set_active_region(self, region_id: Optional[str]) -> None:
        self._active_region = region_id

    def region_count(self) -> int:
        return len(self._communities)

    def regions(self) -> list[str]:
        return list(self._communities.keys())

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _compute_bridge_scores(self) -> None:
        """
        C4: for each concept node, score = fraction of neighbours in other regions.
        A concept that connects two dense communities scores highly.
        """
        if self._graph is None or not self._node_region:
            return
        g = self._graph._g
        self._bridge_scores = {}

        for node_id, region_id in self._node_region.items():
            text = g.nodes[node_id].get('text', '')
            if not text:
                continue
            neighbours  = list(g.neighbors(node_id))
            if not neighbours:
                self._bridge_scores[text] = 0.0
                continue
            other_region = sum(
                1 for nb in neighbours
                if self._node_region.get(nb, region_id) != region_id
            )
            self._bridge_scores[text] = round(other_region / len(neighbours), 4)

        log.debug('bridge scores computed: %d nodes  top=%.3f',
                  len(self._bridge_scores),
                  max(self._bridge_scores.values(), default=0.0))
