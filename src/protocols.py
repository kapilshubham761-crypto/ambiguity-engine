"""
Node [0] — Protocols (Interfaces)
=================================
Python typing.Protocol classes that define the public contracts between
every major component.  No implementation — pure shape.

Why this file exists (SOLID)
----------------------------
Dependency Inversion Principle: high-level components (Teacher, Assessor)
must depend on *abstractions*, not concrete classes.  Any object that
satisfies a protocol can be swapped in — including mocks in tests.

Interface Segregation Principle: each protocol is narrow.  Callers only
import the slice of the interface they actually use.

Protocols used by:
    IGraph        ← Teacher, Assessor
    IQueue        ← Teacher
    IHomework     ← Teacher
    ILessonSource ← Teacher (one entry in SOURCES satisfies this)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


# ======================================================================== #
# IGraph — the knowledge graph surface                                      #
# Components: graph.py (SemanticGraph)                                      #
# ======================================================================== #

@runtime_checkable
class IGraph(Protocol):
    @property
    def node_count(self) -> int: ...
    @property
    def edge_count(self) -> int: ...
    def update(self, concepts: list) -> list[str]: ...
    def save(self) -> None: ...
    def all_nodes(self) -> list[dict]: ...
    def all_edges(self) -> list[dict]: ...


# ======================================================================== #
# IQueue — lesson queue storage                                             #
# Components: queue_mgr.py (LessonQueue)                                   #
# ======================================================================== #

@runtime_checkable
class IQueue(Protocol):
    def add(self, lesson: dict) -> None: ...
    def remove(self, lesson_id: str) -> None: ...
    def list(self) -> list[dict]: ...
    def size(self) -> int: ...
    def known_urls(self) -> set[str]: ...
    def clear(self) -> None: ...


# ======================================================================== #
# IHomework — homework tracking                                             #
# Components: homework.py (HomeworkTracker)                                 #
# ======================================================================== #

@runtime_checkable
class IHomework(Protocol):
    def generate(self, stage: int, graph: IGraph) -> list[dict]: ...
    def tick(self, topic: str) -> None: ...
    def all(self) -> list[dict]: ...
    def pending(self) -> list[dict]: ...


# ======================================================================== #
# ILessonSource — one content provider                                      #
# Components: every entry in sources.SOURCES satisfies this                 #
# ======================================================================== #

class ILessonSource(Protocol):
    def search(self, query: str, max_results: int = 10) -> list[dict]: ...
    def fetch(self, item: dict) -> list[str]: ...
