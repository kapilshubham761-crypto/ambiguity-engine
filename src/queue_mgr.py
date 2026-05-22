"""
Node [C] — Queue Manager
=======================
Single responsibility: manage the lesson queue on disk and in memory.
No fetch logic, no curriculum knowledge, no thread management.

Satisfies IQueue (protocols.py).

Public API
----------
LessonQueue     class   thread-safe queue backed by a JSON file
"""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_ROOT        = os.path.join(os.path.dirname(__file__), '..')
_DEFAULT_PATH = os.path.join(_ROOT, 'data', 'teacher_queue.json')


# ======================================================================== #
# LessonQueue                                                               #
# ======================================================================== #

class LessonQueue:
    """
    Thread-safe, file-backed lesson queue.

    Implements IQueue.  All mutating methods are protected by a single lock
    and immediately persist to disk so that other processes (e.g., the
    Streamlit UI) always see a consistent view.
    """

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self._path  = path
        self._lock  = threading.Lock()
        self._items = self._load()

    # -------------------------------------------------------------------- #
    # Persistence                                                            #
    # -------------------------------------------------------------------- #

    def _load(self) -> list[dict]:
        try:
            with open(self._path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, 'w', encoding='utf-8') as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)

    # -------------------------------------------------------------------- #
    # IQueue interface                                                       #
    # -------------------------------------------------------------------- #

    def add(self, lesson: dict) -> None:
        with self._lock:
            self._items.append(lesson)
            self._save()

    def remove(self, lesson_id: str) -> None:
        with self._lock:
            self._items = [l for l in self._items if l.get('id') != lesson_id]
            self._save()

    def list(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def known_urls(self) -> set[str]:
        with self._lock:
            return {l['url'] for l in self._items if l.get('url')}

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()

    # -------------------------------------------------------------------- #
    # Convenience helpers (not part of IQueue protocol)                     #
    # -------------------------------------------------------------------- #

    def get(self, lesson_id: str) -> dict | None:
        with self._lock:
            return next((l for l in self._items if l['id'] == lesson_id), None)

    def reload(self) -> None:
        """Force-reload from disk (useful after external writes)."""
        with self._lock:
            self._items = self._load()
