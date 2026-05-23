"""
Node [H] — Homework Tracker
==========================
Single responsibility: identify curriculum coverage gaps and track which
topic assignments have been completed.

No queue management, no content fetching, no scheduling.
Satisfies IHomework (protocols.py).

Public API
----------
HomeworkTracker     class   file-backed homework assignment tracker
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

_ROOT        = os.path.join(os.path.dirname(__file__), '..')
_DEFAULT_PATH = os.path.join(_ROOT, 'data', 'homework.json')


# ======================================================================== #
# HomeworkTracker                                                           #
# ======================================================================== #

class HomeworkTracker:
    """
    Persists a list of topic assignments with per-item completion status.
    Implements IHomework.

    Coverage is computed by checking how many topic keywords already appear
    as concept text in the graph — a cheap proxy for "has this been learned".
    """

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self._path  = path
        self._items = self._load()

    # -------------------------------------------------------------------- #
    # Persistence                                                            #
    # -------------------------------------------------------------------- #

    def _load(self) -> list[dict]:
        try:
            with open(self._path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, 'w', encoding='utf-8') as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)

    # -------------------------------------------------------------------- #
    # IHomework interface                                                    #
    # -------------------------------------------------------------------- #

    def generate(self, stage: int, graph, meta=None) -> list[dict]:
        """
        Scan the current stage's topics against known graph concepts.
        Assign the 15 least-covered topics as pending homework.
        Replaces the previous homework list entirely (called after each check-in).
        """
        from curriculum import CURRICULUM

        topics = list(CURRICULUM[stage]['topics'])
        known  = {data['text'].lower() for _, data in graph._g.nodes(data=True)}

        assignments = []
        for topic in topics:
            words    = set(topic.lower().split())
            coverage = len(words & known) / max(len(words), 1)
            assignments.append({
                'id':       str(uuid.uuid4()),
                'topic':    topic,
                'stage':    stage,
                'coverage': round(coverage, 2),
                'status':   'done' if coverage >= 0.5 else 'pending',
                'assigned': datetime.now(tz=timezone.utc).isoformat(),
                'due':      'next check-in',
            })

        # s5-5 — inject hot tension concepts as extra assignments (directed curiosity)
        try:
            from tension import TensionTracker
            hot = TensionTracker.get().hot(n=5)
            existing_topics = {a['topic'].lower() for a in assignments}
            for htopic in hot:
                if htopic.lower() not in existing_topics:
                    words    = set(htopic.lower().split())
                    coverage = len(words & known) / max(len(words), 1)
                    assignments.append({
                        'id':       str(uuid.uuid4()),
                        'topic':    htopic,
                        'stage':    stage,
                        'coverage': round(coverage, 2),
                        'status':   'done' if coverage >= 0.5 else 'pending',
                        'assigned': datetime.now(tz=timezone.utc).isoformat(),
                        'due':      'next check-in',
                        'source':   'tension',
                    })
        except Exception:
            pass

        # B3 — curiosity pull: inject low-familiarity + high-novelty topics
        try:
            import yaml
            with open(os.path.join(_ROOT, 'config.yaml'), encoding='utf-8') as f:
                hw_cfg = yaml.safe_load(f).get('homework', {})
            pull_prob = float(hw_cfg.get('curiosity_pull_prob', 0.20))
        except Exception:
            pull_prob = 0.20

        if pull_prob > 0.0:
            try:
                from novelty import NoveltyTracker
                from tension import TensionTracker
                _nov = NoveltyTracker.get()
                _ten = TensionTracker.get()
                existing_lower = {a['topic'].lower() for a in assignments}
                curiosity_slots = max(1, round(15 * pull_prob))

                # candidates: low-familiarity concepts from graph
                graph_concepts = [data['text'] for _, data in graph._g.nodes(data=True)
                                  if data.get('text', '') not in existing_lower]
                # score: novelty × (1 + tension)
                curiosity_scored = sorted(
                    graph_concepts,
                    key=lambda t: _nov.novelty_score(t) * (1.0 + float(_ten.is_hot(t))),
                    reverse=True
                )
                for topic in curiosity_scored[:curiosity_slots]:
                    if topic.lower() not in existing_lower:
                        words    = set(topic.lower().split())
                        coverage = len(words & known) / max(len(words), 1)
                        assignments.append({
                            'id':       str(uuid.uuid4()),
                            'topic':    topic,
                            'stage':    stage,
                            'coverage': round(coverage, 2),
                            'status':   'done' if coverage >= 0.5 else 'pending',
                            'assigned': datetime.now(tz=timezone.utc).isoformat(),
                            'due':      'next check-in',
                            'source':   'curiosity',
                        })
                        existing_lower.add(topic.lower())
            except Exception:
                pass

        # B4 — escape injection when anti-loop triggers
        try:
            from novelty import NoveltyTracker
            _nov2 = NoveltyTracker.get()
            if _nov2.check_loop():
                excl = {a['topic'].lower() for a in assignments}
                for esc in _nov2.escape_concepts(exclude=excl):
                    words    = set(esc.lower().split())
                    coverage = len(words & known) / max(len(words), 1)
                    assignments.append({
                        'id':       str(uuid.uuid4()),
                        'topic':    esc,
                        'stage':    stage,
                        'coverage': round(coverage, 2),
                        'status':   'pending',
                        'assigned': datetime.now(tz=timezone.utc).isoformat(),
                        'due':      'next check-in',
                        'source':   'escape',
                    })
        except Exception:
            pass

        # s4-4 — bias topic order by meta pressure + inverse coverage
        from attention import bias_topics
        assignments = bias_topics(assignments, meta)
        self._items = assignments[:15]
        self._save()
        return list(self._items)

    def tick(self, topic: str) -> None:
        """Mark a homework item done when its topic has been accepted."""
        changed = False
        for hw in self._items:
            if hw.get('topic', '').lower() == topic.lower() and hw['status'] != 'done':
                hw['status'] = 'done'
                changed = True
        if changed:
            self._save()

    def all(self) -> list[dict]:
        return list(self._items)

    def pending(self) -> list[dict]:
        return [h for h in self._items if h.get('status') == 'pending']

    # -------------------------------------------------------------------- #
    # Convenience                                                            #
    # -------------------------------------------------------------------- #

    def reload(self) -> None:
        self._items = self._load()

    def pending_topics(self) -> list[str]:
        return [h['topic'] for h in self.pending()]
