"""
Phase 5 validation — one input per regime, then a full A/B run.
Run from project root:
    python src/validate_modulator.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import yaml
from extractor import extract
from graph import SemanticGraph
from detector import detect
from modulator import build_prompt, run_ab

CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), '..', 'config.yaml')))

graph = SemanticGraph()

CASES = [
    ("Gradient descent minimises a loss function by iteratively updating model weights.",
     "low",   "tight technical cluster — expect low"),
    ("Memory is the architecture of a house that burned before you were born.",
     "medium", "poetic but coherent — expect medium/high"),
    ("Write a machine-learning model that predicts spiritual enlightenment from user behaviour.",
     "high",   "ML + spirituality = explicit tension — expect high"),
]

print("=" * 64)
print("PHASE 5 — Modulation Layer Validation")
print("=" * 64)

for user_input, expected_level, note in CASES:
    concepts = extract(user_input)
    result   = detect(concepts, graph=graph)
    mod      = build_prompt(concepts, result, graph=graph)

    print(f"\nInput   : {user_input}")
    print(f"Note    : {note}")
    print(f"Concepts: {mod.concept_list}")
    print(f"Score   : {result.score:.3f}  level={result.level}  (expected ~{expected_level})")
    print(f"Neighbours injected: {mod.neighbours or ['(none)']}")
    print(f"--- System prompt ---")
    print(mod.system_prompt[:300])
    print("---")

# Full A/B run on the high-tension input
print("\n" + "=" * 64)
print("A/B RUN — high-tension input")
print("=" * 64)

ab_input   = "Write a machine-learning model that predicts spiritual enlightenment from user behaviour."
ab_concepts = extract(ab_input)
ab_result   = detect(ab_concepts, graph=graph)
entry       = run_ab(ab_input, ab_concepts, ab_result, graph, CFG)

print(f"\nAmbiguity : {entry['ambiguity_score']:.3f} ({entry['ambiguity_level']})")
print(f"Concepts  : {entry['concepts']}")
print(f"Neighbours: {entry['neighbours']}")
print(f"\n[MODULATED OUTPUT]\n{entry['modulated_output']}")
print(f"\n[CONTROL OUTPUT]\n{entry['control_output']}")
print(f"\nA/B log  : logs/ab_log.jsonl")
