"""
Pause II — Baseline Eval Suite
================================
Runs 30 fixed prompts through run_ab() with attention.bias_strength=0.
Records results to logs/ab_log.jsonl and prints a summary.

Run BEFORE Step 04 to capture the pre-attention baseline win-rate.
Re-run after Step 04 with bias_strength > 0 to measure the delta.

Usage:
    python src/eval_baseline.py
    python src/eval_baseline.py --judge   # also write ab_judgments stub
"""

import sys, os, json, argparse
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))

ROOT = os.path.join(os.path.dirname(__file__), '..')

import yaml
from extractor import extract
from detector import detect_and_log
from modulator import run_ab
from graph import SemanticGraph
from meta_state import MetaState

CFG = yaml.safe_load(open(os.path.join(ROOT, 'config.yaml'), encoding='utf-8'))

# ── 30 fixed prompts — low / medium / high ambiguity spread ──────────────────
# 10 low  (clear factual questions — unambiguous concepts)
# 10 medium (relational / contextual — some spread)
# 10 high  (abstract / multi-meaning — pulls in different directions)

PROMPTS = [
    # LOW — 10
    "What is the boiling point of water?",
    "How many planets are in the solar system?",
    "What year did World War II end?",
    "Who wrote Romeo and Juliet?",
    "What is the chemical formula for salt?",
    "How far is the Moon from Earth?",
    "What is the speed of light?",
    "Who painted the Mona Lisa?",
    "What is the capital of Japan?",
    "How many bones are in the human body?",

    # MEDIUM — 10
    "How does the immune system fight infection?",
    "Why do seasons change?",
    "What causes lightning?",
    "How does memory work in the brain?",
    "Why do some metals conduct electricity?",
    "How does a vaccine protect against disease?",
    "What is the relationship between energy and mass?",
    "How do plants know when to flower?",
    "Why does the ocean appear blue?",
    "How does natural selection drive evolution?",

    # HIGH — 10
    "Is free will compatible with determinism?",
    "What is the nature of consciousness?",
    "Can mathematics be considered a language?",
    "Is time travel theoretically possible?",
    "What distinguishes life from non-life?",
    "How does meaning emerge from language?",
    "Is the universe fundamentally mathematical?",
    "What is the relationship between knowledge and belief?",
    "Can artificial systems ever be truly creative?",
    "What makes a moral action right or wrong?",
]

assert len(PROMPTS) == 30


def run_suite(judge: bool = False) -> None:
    print(f"\n── Pause II Baseline Eval  (bias_strength=0) ──────────────────")
    print(f"   {len(PROMPTS)} prompts · writing to logs/ab_log.jsonl\n")

    g   = SemanticGraph()
    ms  = MetaState.get()

    passed = failed = 0
    level_counts = {'low': 0, 'medium': 0, 'high': 0}
    judgments = []

    for i, prompt in enumerate(PROMPTS, 1):
        sys.stdout.write(f"  [{i:02d}/{len(PROMPTS)}] {prompt[:55]:<55}")
        sys.stdout.flush()
        try:
            concepts = extract(prompt)
            result   = detect_and_log(prompt, concepts, graph=g)
            entry    = run_ab(prompt, concepts, result, g, CFG, meta=ms)

            level = entry['ambiguity_level']
            level_counts[level] = level_counts.get(level, 0) + 1

            print(f"  {level:<6}  score={entry['ambiguity_score']:.3f}  "
                  f"nb={len(entry['neighbours'])}  "
                  f"meta={len(entry.get('meta_concepts', []))}")
            passed += 1

            if judge:
                judgments.append({
                    'prompt_idx': i,
                    'prompt':     prompt,
                    'level':      level,
                    'score':      entry['ambiguity_score'],
                    'winner':     None,   # to be filled by human judge
                    'note':       '',
                })

        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n── Summary ──────────────────────────────────────────────────────")
    print(f"   Ran:    {passed}/{len(PROMPTS)}")
    print(f"   Failed: {failed}")
    print(f"   Levels: low={level_counts.get('low',0)}  "
          f"medium={level_counts.get('medium',0)}  "
          f"high={level_counts.get('high',0)}")
    print(f"\n   Baseline recorded. Re-run after Step 04 with bias_strength > 0")
    print(f"   to measure attention delta.")
    print(f"   Results in: logs/ab_log.jsonl")

    if judge:
        jpath = os.path.join(ROOT, 'logs', 'ab_judgments.jsonl')
        os.makedirs(os.path.dirname(jpath), exist_ok=True)
        with open(jpath, 'a', encoding='utf-8') as f:
            for j in judgments:
                f.write(json.dumps(j, ensure_ascii=False) + '\n')
        print(f"   Judgment stubs: logs/ab_judgments.jsonl")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--judge', action='store_true',
                        help='Write blank judgment stubs to ab_judgments.jsonl')
    args = parser.parse_args()
    run_suite(judge=args.judge)
