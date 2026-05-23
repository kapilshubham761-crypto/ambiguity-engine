"""
Node [R] — Region Index (stub)
================================
Deferred until the semantic graph crosses ~50k nodes. At that scale, partitioning
the graph into regions enables subgraph-scoped retrieval and region-level attention.

Until then: RegionIndex is a protocol-compliant no-op.
  assign()        → no-op (no partitioning at current graph size)
  active_region() → None (whole-graph mode)
  nodes_in(None)  → all node IDs (passes through to full graph)

Swap-in path: replace RegionIndex with a real implementation (e.g. Louvain
community detection via networkx-community) and inject via DI — no consumer
changes required, as they talk to IRegions only.

Implements IRegions (protocols.py [0]).
"""

from __future__ import annotations

from typing import Optional

from protocols import IRegions


class RegionIndex:
    """No-op region index — whole-graph mode until ~50k nodes."""

    def __init__(self, graph=None) -> None:
        self._graph = graph

    def assign(self) -> None:
        """Partition graph into regions. No-op at current scale."""
        pass

    def active_region(self) -> None:
        """Return the currently active region. Always None in whole-graph mode."""
        return None

    def nodes_in(self, region: object) -> list:
        """
        Return node IDs belonging to `region`.
        In whole-graph mode (region=None), returns all node IDs.
        """
        if self._graph is None:
            return []
        return list(self._graph._g.nodes())
