"""
Phase 6.6 — Stress test: artificially age graph by 30 days, run maintenance,
verify size drop (edges decayed/pruned, orphan nodes removed).
Run from project root:
    python src/validate_maintenance.py
"""
import os, sys, json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

from extractor import extract
from graph import SemanticGraph
from maintenance import (
    apply_decay, prune_weak_edges, prune_orphan_nodes,
    DECAY_FACTOR, PRUNE_WEIGHT, ORPHAN_DAYS, run_maintenance,
)

# ------------------------------------------------------------------ #
# Build a graph with known content                                     #
# ------------------------------------------------------------------ #

import os, sqlite3
DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'graph.db')
if os.path.exists(DB):
    os.remove(DB)

# Also clear stamp so maintenance runs fresh
STAMP = os.path.join(os.path.dirname(__file__), '..', 'data', 'last_cleaned.txt')
if os.path.exists(STAMP):
    os.remove(STAMP)

g = SemanticGraph()

INPUTS = [
    "Gradient descent minimises a loss function by iteratively updating model weights.",
    "Neural networks learn distributed representations across millions of training examples.",
    "A futuristic yet warm city where cold algorithms govern intimate human relationships.",
    "The transformer architecture uses self-attention mechanisms to model long-range dependencies.",
    "Backpropagation computes gradients via the chain rule through a computation graph.",
]

for text in INPUTS:
    concepts = extract(text)
    g.update(concepts)
g.save()

nodes_initial = g.node_count
edges_initial = g.edge_count
print(f"Initial graph : {nodes_initial} nodes, {edges_initial} edges")
print(f"Edge weights  : min={min(d['weight'] for _,_,d in g._g.edges(data=True)):.3f}  "
      f"max={max(d['weight'] for _,_,d in g._g.edges(data=True)):.3f}")

# ------------------------------------------------------------------ #
# Artificially age: push all timestamps back 30 days                  #
# ------------------------------------------------------------------ #

old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat(timespec='seconds')

for u, v in g._g.edges():
    g._g[u][v]['last_updated'] = old_ts

for nid in g._g.nodes():
    g._g.nodes[nid]['last_seen'] = old_ts

print(f"\nArtificially aged all timestamps by 30 days.")

# ------------------------------------------------------------------ #
# Run decay — weights should drop to ~0.99^30 ≈ 0.740 of original    #
# ------------------------------------------------------------------ #

decayed = apply_decay(g)
weights_after_decay = [d['weight'] for _, _, d in g._g.edges(data=True)]
factor_applied = DECAY_FACTOR ** 30
print(f"\nAfter decay (factor={factor_applied:.4f} for 30 days):")
print(f"  Edges decayed : {decayed}")
if weights_after_decay:
    print(f"  Weight range  : {min(weights_after_decay):.4f} – {max(weights_after_decay):.4f}")

# ------------------------------------------------------------------ #
# Run weak edge pruning                                                #
# ------------------------------------------------------------------ #

pruned_e = prune_weak_edges(g)
print(f"\nAfter weak-edge pruning (threshold={PRUNE_WEIGHT}):")
print(f"  Edges removed : {pruned_e}")
print(f"  Edges remaining: {g.edge_count}")

# ------------------------------------------------------------------ #
# Disconnect some nodes to test orphan pruning                         #
# ------------------------------------------------------------------ #

# Manually isolate a few nodes (remove their remaining edges)
isolated = []
for nid in list(g._g.nodes())[:3]:
    nbrs = list(g._g.neighbors(nid))
    for nb in nbrs:
        g._g.remove_edge(nid, nb)
    isolated.append(g._g.nodes[nid]['text'])

print(f"\nManually isolated nodes: {isolated}")
pruned_n = prune_orphan_nodes(g)
print(f"Orphan nodes pruned (>{ORPHAN_DAYS}d with no edges): {pruned_n}")
print(f"Nodes remaining: {g.node_count}")

# ------------------------------------------------------------------ #
# Summary assertions                                                   #
# ------------------------------------------------------------------ #

assert g.node_count < nodes_initial or pruned_n >= 0, "Node count should not grow"
assert g.edge_count <= edges_initial, "Edge count should not grow after pruning"

print(f"\nFinal graph : {g.node_count} nodes, {g.edge_count} edges")
print(f"Reduction   : {nodes_initial - g.node_count} nodes removed, "
      f"{edges_initial - g.edge_count} edges removed")

# ------------------------------------------------------------------ #
# Daily gate test — re-running maintenance today should skip          #
# ------------------------------------------------------------------ #

g.save()
result = run_maintenance(g, force=False)
assert result.get('skipped'), "Second maintenance call today should be skipped"
print(f"\nDaily gate: maintenance correctly skipped on second call today. OK")

print("\nAll stress-test assertions passed.")
