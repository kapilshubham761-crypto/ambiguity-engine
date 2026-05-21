"""
Phase 4.2 — Calibration across 30 inputs.
Expects: technical < 0.3, poetic > 0.5, tension > 0.6
Run from project root:
    python src/validate_detector.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from extractor import extract
from graph import SemanticGraph
from detector import detect_and_log

# ------------------------------------------------------------------ #
# 4.2a — 10 technical inputs (expect < 0.3)                          #
# ------------------------------------------------------------------ #
TECHNICAL = [
    "Gradient descent minimises a loss function by iteratively updating model weights.",
    "A convolutional layer applies learned filters across a spatial input tensor.",
    "The quicksort algorithm recursively partitions an array around a pivot element.",
    "TCP/IP uses a three-way handshake to establish a reliable connection between hosts.",
    "A relational database stores data in normalised tables linked by foreign keys.",
    "Transformer encoders compute multi-head self-attention over token embeddings.",
    "Hash functions map arbitrary-length inputs to fixed-size digests deterministically.",
    "Backpropagation computes gradients via the chain rule through a computation graph.",
    "A virtual machine emulates hardware to isolate process execution environments.",
    "HTTPS encrypts HTTP traffic using TLS certificates and asymmetric key exchange.",
]

# ------------------------------------------------------------------ #
# 4.2b — 10 poetic inputs (expect > 0.5)                             #
# ------------------------------------------------------------------ #
POETIC = [
    "The moon dissolves in rust; the clock forgets its hands.",
    "Grief is a country with no roads, only weather.",
    "She carried silence the way rivers carry winter light.",
    "Every door I open leads to a field of abandoned instruments.",
    "Time is a dog that eats itself in the corner of the room.",
    "The word for longing has no translation; it tastes like iron and bread.",
    "Stars are the punctuation of a sentence no one has finished writing.",
    "He dreamed in colours that do not exist outside of fever.",
    "Memory is the architecture of a house that burned before you were born.",
    "Love arrives like a power outage — total, and strangely peaceful.",
]

# ------------------------------------------------------------------ #
# 4.2c — 10 tension / bridge prompts (expect high)                   #
# ------------------------------------------------------------------ #
TENSION = [
    "A futuristic yet warm city where cold algorithms govern the most intimate human relationships.",
    "The ancient ritual was live-streamed to three million followers on a social media platform.",
    "Write a machine-learning model that predicts spiritual enlightenment from user behaviour.",
    "The soldier planted flowers in the craters left by his own artillery.",
    "A corporation that runs entirely on trust, zero contracts, pure reputation.",
    "The child prodigy retired at twenty, exhausted by the future everyone else wanted.",
    "Science and prayer agree: the body knows things the mind refuses to believe.",
    "Brutal honesty delivered with radical tenderness — is that even possible?",
    "The algorithm was trained on love letters and court transcripts simultaneously.",
    "She coded her grief into a game where the only winning move is to stop playing.",
]

def run_batch(label, inputs, graph):
    scores = []
    print(f'\n[{label}]')
    for text in inputs:
        concepts = extract(text)
        result = detect_and_log(text, concepts, graph=graph)
        scores.append(result.score)
        bar = '+' * int(result.score * 40)
        print(f'  {result.score:.3f} ({result.level:6})  {bar}  | {text[:55]}')
    avg = sum(scores) / len(scores)
    print(f'  --- avg={avg:.3f}  min={min(scores):.3f}  max={max(scores):.3f}')
    return scores

graph = SemanticGraph()

tech_scores    = run_batch('TECHNICAL (expect avg < 0.30)', TECHNICAL, graph)
poetic_scores  = run_batch('POETIC    (expect avg > 0.50)', POETIC,    graph)
tension_scores = run_batch('TENSION   (expect avg > 0.60)', TENSION,   graph)

print('\n--- Calibration summary ---')
tech_avg    = sum(tech_scores)    / len(tech_scores)
poetic_avg  = sum(poetic_scores)  / len(poetic_scores)
tension_avg = sum(tension_scores) / len(tension_scores)

print(f'  Technical avg : {tech_avg:.3f}   {"OK" if tech_avg < 0.30 else "NEEDS TUNING"}')
print(f'  Poetic    avg : {poetic_avg:.3f}   {"OK" if poetic_avg > 0.50 else "NEEDS TUNING"}')
print(f'  Tension   avg : {tension_avg:.3f}   {"OK" if tension_avg > 0.60 else "NEEDS TUNING"}')
print(f'\nThresholds locked (4.2d): low < 0.3 | medium 0.3-0.6 | high > 0.6')
print(f'Score log: logs/ambiguity_scores.jsonl')
