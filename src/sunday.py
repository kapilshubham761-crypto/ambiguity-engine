"""
Sunday — no new input, decay only.
Phase 8 · Day 7: test the cleaning operators.

Run from project root:
    python src/sunday.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from graph import SemanticGraph
from maintenance import run_maintenance

print("\nSunday run — decay and pruning only, no new input.")
print("─" * 50)

g = SemanticGraph()

print(f"Before : {g.node_count} nodes, {g.edge_count} edges")

result = run_maintenance(g, force=True)

print(f"After  : {g.node_count} nodes, {g.edge_count} edges")
print(f"  Edges decayed  : {result['edges_decayed']}")
print(f"  Edges pruned   : {result['edges_pruned']}")
print(f"  Nodes pruned   : {result['nodes_pruned']}")

snap = g.snapshot()
print(f"  Snapshot       : {snap}")
print()
