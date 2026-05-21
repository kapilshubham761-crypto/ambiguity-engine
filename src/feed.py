"""
Batch feeder — push a text file through the full pipeline.

Usage:
    python src/feed.py data/monday.txt
    python src/feed.py data/monday.txt --label "Mon Wikipedia"

One input per non-empty line. Lines starting with # are treated as comments.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from extractor import extract
from graph import SemanticGraph
from detector import detect_and_log

# --------------------------------------------------------------------------- #

def feed(path: str, label: str | None = None) -> None:
    if not os.path.exists(path):
        print(f"File not found: {path}")
        sys.exit(1)

    lines = [
        l.strip() for l in open(path, encoding='utf-8')
        if l.strip() and not l.strip().startswith('#')
    ]
    if not lines:
        print("File is empty or contains only comments.")
        sys.exit(0)

    tag = label or os.path.basename(path)
    print(f"\nFeeding: {tag}  ({len(lines)} inputs)")
    print("─" * 60)

    g = SemanticGraph()

    total_concepts = 0
    scores         = []
    level_counts   = {'low': 0, 'medium': 0, 'high': 0}

    for i, text in enumerate(lines, 1):
        t0       = time.time()
        concepts = extract(text)
        result   = detect_and_log(text, concepts, graph=g)
        g.update(concepts)
        elapsed  = time.time() - t0

        total_concepts += len(concepts)
        scores.append(result.score)
        level_counts[result.level] += 1

        bar   = '█' * int(result.score * 20)
        print(f"  {i:3}.  [{result.level:6}]  {result.score:.3f}  {bar:<20}  "
              f"({len(concepts)} concepts)  {elapsed:.1f}s")
        print(f"         {text[:72]}")

    # ------------------------------------------------------------------ #
    # Save & snapshot                                                      #
    # ------------------------------------------------------------------ #
    g.save()
    snap = g.snapshot()

    # ------------------------------------------------------------------ #
    # Summary                                                              #
    # ------------------------------------------------------------------ #
    avg  = sum(scores) / len(scores)
    low  = level_counts['low']
    med  = level_counts['medium']
    high = level_counts['high']

    print("\n" + "─" * 60)
    print(f"  Inputs processed : {len(lines)}")
    print(f"  Concepts added   : {total_concepts}")
    print(f"  Graph now        : {g.node_count} nodes, {g.edge_count} edges")
    print(f"  Avg ambiguity    : {avg:.3f}  "
          f"(low={low}  medium={med}  high={high})")
    print(f"  Snapshot         : {snap}")
    print()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Feed a text file into the Ambiguity Engine.')
    parser.add_argument('file',          help='Path to input file (one line per input)')
    parser.add_argument('--label', '-l', help='Human-readable label for this batch', default=None)
    args = parser.parse_args()
    feed(args.file, label=args.label)
