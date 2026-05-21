"""
Phase 3.4d — Round-trip test.
5 inputs -> save -> reload -> verify counts and node texts are intact.
Run from project root with venv active:
    python src/validate_graph.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))

from extractor import extract
from graph import SemanticGraph

INPUTS = [
    "The transformer architecture uses self-attention mechanisms to model long-range dependencies.",
    "OpenAI and Google are competing fiercely in the artificial intelligence market.",
    "A futuristic yet warm city where cold algorithms govern intimate human relationships.",
    "Neural networks learn distributed representations across millions of training examples.",
    "The tension between efficiency and creativity drives most engineering decisions.",
]

# ---------------------------------------------------------- #
# Pass 1: fresh graph, ingest 5 inputs, save                 #
# ---------------------------------------------------------- #

# Wipe the DB so this test is repeatable
DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'graph.db')
if os.path.exists(DB):
    os.remove(DB)
    print(f'Cleared existing DB: {DB}')

g1 = SemanticGraph()
all_node_ids: set[str] = set()

for i, text in enumerate(INPUTS, 1):
    concepts = extract(text)
    ids = g1.update(concepts)
    all_node_ids.update(ids)
    print(f'  Input {i}: {len(concepts)} concepts -> {len(ids)} node IDs resolved')

g1.save()
snap = g1.snapshot()

nodes_before = g1.node_count
edges_before = g1.edge_count
texts_before = {data['text'] for _, data in g1._g.nodes(data=True)}

print(f'\nAfter 5 inputs: {nodes_before} nodes, {edges_before} edges')
print(f'Snapshot: {snap}')

# ---------------------------------------------------------- #
# Pass 2: reload from DB (simulates a restart)               #
# ---------------------------------------------------------- #

del g1
g2 = SemanticGraph()

nodes_after = g2.node_count
edges_after = g2.edge_count
texts_after = {data['text'] for _, data in g2._g.nodes(data=True)}

print(f'After reload : {nodes_after} nodes, {edges_after} edges')

# ---------------------------------------------------------- #
# Assertions                                                  #
# ---------------------------------------------------------- #

assert nodes_after == nodes_before, \
    f"Node count mismatch: {nodes_before} -> {nodes_after}"
assert edges_after == edges_before, \
    f"Edge count mismatch: {edges_before} -> {edges_after}"
assert texts_after == texts_before, \
    f"Node text mismatch:\n  lost: {texts_before - texts_after}\n  gained: {texts_after - texts_before}"

print('\nAll assertions passed. Round-trip integrity: OK')

# ---------------------------------------------------------- #
# Quick neighbourhood sample                                  #
# ---------------------------------------------------------- #

first_id = next(iter(g2._g.nodes()))
first_text = g2._g.nodes[first_id]['text']
neighbors = g2.get_neighbors(first_id, top_k=5)
print(f'\nSample node: "{first_text}"')
print(f'  Top neighbors:')
for n in neighbors:
    print(f'    "{n["text"]}"  weight={n["weight"]:.3f}')
