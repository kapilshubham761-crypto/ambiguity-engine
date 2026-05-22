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

    def generate(self, stage: int, graph) -> list[dict]:
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

        assignments.sort(key=lambda a: a['coverage'])   # least-known first
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
