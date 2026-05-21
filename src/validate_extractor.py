"""
Phase 2.3 — Eyeball validation across input types.
Run from project root with venv active:
    python src/validate_extractor.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from extractor import extract

INPUTS = {
    'technical': (
        "The transformer architecture uses self-attention mechanisms "
        "to model long-range dependencies in sequential data."
    ),
    'poetic': (
        "A grief ago, she who was who I hold, the fats and flower, "
        "or, water-lammed, from the scythe-sided thorn."
    ),
    'tension': (
        "A futuristic yet warm city where cold algorithms govern "
        "the most intimate human relationships."
    ),
    'news': (
        "OpenAI and Google are competing fiercely in the artificial "
        "intelligence market, with Microsoft investing heavily in both."
    ),
    'conversational': (
        "I mean, the thing is, people just don't really get the point "
        "of the whole situation, you know what I mean?"
    ),
}

WIDTH = 28

for label, text in INPUTS.items():
    concepts = extract(text)
    print(f'\n[{label.upper()}]')
    print(f'  Input : {text[:80]}…')
    print(f'  Found : {len(concepts)} concepts')
    for c in concepts:
        bar = '#' * int(abs(c.embedding[0]) * 12)
        print(f'    {c.source:6}  {c.text:{WIDTH}}  emb[0]={c.embedding[0]:+.3f}  {bar}')

from extractor import cache_size
print(f'\nCache size after all inputs: {cache_size()} entries')
