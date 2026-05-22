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


# ======================================================================== #
# IMetaState — temporary concept activation layer                          #
# Components: meta_state.py (MetaState)                                    #
# ======================================================================== #

@runtime_checkable
class IMetaState(Protocol):
    def reinforce(self, texts: list[str], gain: float | None = None) -> None: ...
    def decay_to(self, now: object | None = None) -> None: ...
    def active(self, threshold: float | None = None) -> dict: ...
    def top(self, n: int = 10) -> list: ...
    def snapshot(self) -> dict: ...


# ======================================================================== #
# IAttention — activation-weighted candidate reranking                     #
# Components: attention.py (AttentionBias)  — built in Step 04             #
# ======================================================================== #

@runtime_checkable
class IAttention(Protocol):
    def bias(self, candidates: list, scores: list[float],
             meta: IMetaState, strength: float) -> list: ...


# ======================================================================== #
# ITension — rolling-window ambiguity tension tracker                      #
# Components: tension.py (TensionTracker)  — built in Step 05              #
# ======================================================================== #

@runtime_checkable
class ITension(Protocol):
    def observe(self, concept: str, score: float) -> None: ...
    def hot(self, n: int = 5) -> list: ...
    def is_hot(self, concept: str) -> bool: ...


# ======================================================================== #
# IRegions — graph region index (stub until ~50k nodes)                   #
# Components: regions.py (RegionIndex)     — built in Step 06              #
# ======================================================================== #

@runtime_checkable
class IRegions(Protocol):
    def assign(self) -> None: ...
    def active_region(self) -> object: ...
    def nodes_in(self, region: object) -> list: ...
