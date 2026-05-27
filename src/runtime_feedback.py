"""
Runtime Memory Feedback
=========================
Every runtime result (runner pipeline output) returns a structured feedback
record that is fed back into the cognitive architecture.

Without this, execution is stateless — actions don't create experience.
With this, every action changes cognition.

Feedback schema:
    {
      job_id:              str
      ts:                  float  unix timestamp
      prompt:              str    original input
      response_text:       str    LLM output (trimmed)
      success:             float  [0, 1]  did the response resolve the intent?
      novelty:             float  [0, 1]  how unexpected was the output?
      friction:            float  [0, 1]  how hard was it to produce?
      unexpected_outcome:  list[str]  concept texts that appeared unexpectedly
      semantic_shift:      list[str]  concepts that changed activation significantly
      goal_progress:       float  [0, 1]  contribution to active goal
      concepts_in:         list[str]  concepts extracted from prompt
      concepts_out:        list[str]  concepts extracted from response
      ambiguity_in:        float  ambiguity score of input
      ambiguity_out:       float  ambiguity score of output (recalculated)
      llm_ms:              int    LLM latency in ms
    }

Feed-forward effects:
    JAM field    — boost concepts_out by success; decay concepts that caused friction
    Worldview    — record goal_progress against current goal/mode
    Contradiction — register unexpected concept pairs as contradiction candidates
    Predictor    — reinforce transitions from concepts_in to concepts_out
    GoalGraph    — activate goal nodes that match goal_progress signal

Public API:
    RuntimeFeedback.get()                                singleton
    record(job_id, prompt, response_text, meta) → dict  compute + store feedback
    recent(n) → list[dict]                               last n feedback records
    snapshot() → dict                                    summary stats
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import deque
from typing import Optional

from logger import get_logger

log = get_logger('runtime_feedback')

_ROOT      = os.path.join(os.path.dirname(__file__), '..')
_DATA_PATH = os.path.join(_ROOT, 'data', 'runtime_feedback.jsonl')

_SINGLETON: Optional['RuntimeFeedback'] = None
_ROLLING_MAX = 200


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ══════════════════════════════════════════════════════════════════════════════
# RuntimeFeedback
# ══════════════════════════════════════════════════════════════════════════════

class RuntimeFeedback:

    def __init__(self) -> None:
        self._history: deque[dict] = deque(maxlen=_ROLLING_MAX)
        self._total = 0
        self._mean_success   = 0.5
        self._mean_novelty   = 0.5
        self._mean_friction  = 0.3
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def record(self, job_id: str, prompt: str, response_text: str,
               meta: dict) -> dict:
        """
        Compute a feedback record from runner output and feed it into all
        downstream cognitive systems.

        meta keys (from runner pipeline):
            concepts_in   list[str]   concept texts from prompt extraction
            ambiguity_in  float       ambiguity score of prompt
            llm_ms        int         LLM latency ms
            strategy      str         modulator prompt regime (low/medium/high)
        """
        concepts_in   = meta.get('concepts_in', [])
        ambiguity_in  = float(meta.get('ambiguity_in', 0.3))
        llm_ms        = int(meta.get('llm_ms', 0))
        strategy      = meta.get('strategy', 'medium')

        # Extract concepts from response text
        concepts_out = self._extract_concepts(response_text, concepts_in)

        # Compute feedback metrics
        success      = self._compute_success(response_text, concepts_in, ambiguity_in)
        novelty      = self._compute_novelty(concepts_out, concepts_in)
        friction     = self._compute_friction(llm_ms, ambiguity_in, strategy)
        unexpected   = self._compute_unexpected(concepts_out, concepts_in)
        sem_shift    = self._compute_semantic_shift(concepts_in, concepts_out)
        goal_prog    = self._compute_goal_progress(success, novelty, friction)
        ambiguity_out = self._compute_ambiguity(concepts_out)

        fb = {
            'job_id':             job_id,
            'ts':                 time.time(),
            'prompt':             prompt[:200],
            'response_text':      response_text[:400],
            'success':            round(success, 3),
            'novelty':            round(novelty, 3),
            'friction':           round(friction, 3),
            'unexpected_outcome': unexpected[:8],
            'semantic_shift':     sem_shift[:8],
            'goal_progress':      round(goal_prog, 3),
            'concepts_in':        concepts_in[:12],
            'concepts_out':       concepts_out[:12],
            'ambiguity_in':       round(ambiguity_in, 3),
            'ambiguity_out':      round(ambiguity_out, 3),
            'llm_ms':             llm_ms,
        }

        self._history.append(fb)
        self._total += 1
        self._update_running_stats(success, novelty, friction)
        self._feed_forward(fb)
        self._append_to_log(fb)

        log.debug('feedback: success=%.2f novelty=%.2f friction=%.2f goal_prog=%.2f',
                  success, novelty, friction, goal_prog)
        return fb

    def recent(self, n: int = 20) -> list[dict]:
        items = list(self._history)
        return items[-n:]

    def snapshot(self) -> dict:
        return {
            'total_records':  self._total,
            'mean_success':   round(self._mean_success, 3),
            'mean_novelty':   round(self._mean_novelty, 3),
            'mean_friction':  round(self._mean_friction, 3),
            'recent':         self.recent(5),
        }

    @classmethod
    def get(cls) -> 'RuntimeFeedback':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ── Metric computation ─────────────────────────────────────────────────────

    def _extract_concepts(self, text: str, fallback: list[str]) -> list[str]:
        try:
            from extractor import extract
            concepts = extract(text)
            texts = [c.text for c in concepts if len(c.text) > 3]
            return texts[:12] if texts else fallback[:6]
        except Exception:
            return fallback[:6]

    def _compute_success(self, response: str, concepts_in: list[str],
                         ambiguity: float) -> float:
        if not response or len(response) < 20:
            return 0.1
        # Heuristics: length, concept coverage, coherence markers
        length_score = _clamp(len(response) / 800.0)
        coverage = 0.0
        if concepts_in:
            r_lower = response.lower()
            hits = sum(1 for c in concepts_in if c.lower() in r_lower)
            coverage = hits / len(concepts_in)
        coherence_markers = ['therefore', 'because', 'however', 'thus', 'this means',
                             'which suggests', 'in contrast', 'as a result']
        coherence_score = min(sum(1 for m in coherence_markers if m in response.lower()) / 3.0, 1.0)
        # Lower ambiguity input = clearer task = success is more binary
        success = (length_score * 0.3 + coverage * 0.4 + coherence_score * 0.3)
        return _clamp(success)

    def _compute_novelty(self, concepts_out: list[str],
                         concepts_in: list[str]) -> float:
        if not concepts_out:
            return 0.0
        in_set = set(c.lower() for c in concepts_in)
        new = [c for c in concepts_out if c.lower() not in in_set]
        novelty = len(new) / max(len(concepts_out), 1)
        # Also check against JAM field — if concepts_out are low-activation, they're novel
        try:
            from jam_field import JamField
            active = JamField.get().active(threshold=0.05)
            field_novel = sum(1 for c in concepts_out if c not in active)
            field_novelty = field_novel / max(len(concepts_out), 1)
            novelty = (novelty + field_novelty) / 2.0
        except Exception:
            pass
        return _clamp(novelty)

    def _compute_friction(self, llm_ms: int, ambiguity: float,
                          strategy: str) -> float:
        # High latency + high ambiguity = high friction
        latency_friction = _clamp(llm_ms / 10000.0)  # 10s = max friction
        ambiguity_friction = ambiguity * 0.4
        strategy_weight = {'low': 0.1, 'medium': 0.3, 'high': 0.5}.get(strategy, 0.3)
        return _clamp(latency_friction * 0.4 + ambiguity_friction * 0.4 + strategy_weight * 0.2)

    def _compute_unexpected(self, concepts_out: list[str],
                             concepts_in: list[str]) -> list[str]:
        in_set = set(c.lower() for c in concepts_in)
        try:
            from embed_index import EmbeddingIndex
            idx = EmbeddingIndex.get()
            unexpected = []
            for c in concepts_out:
                if c.lower() in in_set:
                    continue
                # Unexpected if it's NOT a near neighbor of any input concept
                is_expected = False
                for ci in concepts_in[:4]:
                    neighbors = [t for t, _ in idx.knn_text(ci, k=6)]
                    if c in neighbors:
                        is_expected = True
                        break
                if not is_expected:
                    unexpected.append(c)
            return unexpected
        except Exception:
            return [c for c in concepts_out if c.lower() not in in_set][:4]

    def _compute_semantic_shift(self, concepts_in: list[str],
                                concepts_out: list[str]) -> list[str]:
        try:
            from jam_field import JamField
            field = JamField.get()
            snap_before = {c: field.active().get(c, 0.0) for c in concepts_in}
            # concepts that now appear in the field that weren't prominent before
            active_now = field.active(threshold=0.1)
            shift = []
            for c, act in active_now.items():
                prev = snap_before.get(c, 0.0)
                if act - prev > 0.15:
                    shift.append(c)
            return shift[:8]
        except Exception:
            return []

    def _compute_goal_progress(self, success: float, novelty: float,
                               friction: float) -> float:
        return _clamp(success * 0.5 + novelty * 0.3 - friction * 0.2)

    def _compute_ambiguity(self, concepts: list[str]) -> float:
        if len(concepts) < 2:
            return 0.0
        try:
            from embed_index import EmbeddingIndex
            idx = EmbeddingIndex.get()
            vecs = [idx.get_embedding(c) for c in concepts[:8]]
            vecs = [v for v in vecs if v is not None]
            if len(vecs) < 2:
                return 0.0
            import numpy as np
            mat  = np.stack(vecs)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            mat  = mat / (norms + 1e-8)
            sims = mat @ mat.T
            n = len(vecs)
            upper = [(i, j) for i in range(n) for j in range(i+1, n)]
            dists = [1.0 - float(sims[i, j]) for i, j in upper]
            return _clamp(sum(dists) / len(dists) if dists else 0.0)
        except Exception:
            return 0.0

    # ── Feed-forward effects ───────────────────────────────────────────────────

    def _feed_forward(self, fb: dict) -> None:
        self._feed_jam(fb)
        self._feed_worldview(fb)
        self._feed_contradictions(fb)
        self._feed_predictor(fb)
        self._feed_goal_graph(fb)

    def _feed_jam(self, fb: dict) -> None:
        try:
            from jam_field import JamField
            field  = JamField.get()
            active = field.active(threshold=0.01)
            updates = {}
            for c in fb['concepts_out']:
                if c in active:
                    delta = fb['success'] * 0.08 - fb['friction'] * 0.04
                    updates[c] = {'activation': _clamp(active[c] + delta)}
            for c in fb['unexpected_outcome']:
                if c in active:
                    updates[c] = {'novelty': _clamp(active.get(c, 0.1) + 0.1)}
            if updates:
                field.update_dynamics(updates)
        except Exception as e:
            log.debug('feed_jam error: %s', e)

    def _feed_worldview(self, fb: dict) -> None:
        try:
            from worldview import Worldview
            from regulation import Regulation
            reg = Regulation.get()
            wv  = Worldview.get()
            wv.record_goal(reg.goal, reg.mode)
        except Exception as e:
            log.debug('feed_worldview error: %s', e)

    def _feed_contradictions(self, fb: dict) -> None:
        try:
            from contradiction import ContradictionRegistry
            unexpected = fb.get('unexpected_outcome', [])
            if len(unexpected) >= 2:
                cr = ContradictionRegistry.get()
                cr.observe(unexpected)
        except Exception as e:
            log.debug('feed_contradictions error: %s', e)

    def _feed_predictor(self, fb: dict) -> None:
        try:
            from episodes import EpisodeStore
            concepts_in  = fb.get('concepts_in', [])
            concepts_out = fb.get('concepts_out', [])
            if concepts_in and concepts_out:
                store = EpisodeStore.get()
                store.record(concepts_in + concepts_out,
                             ambiguity=fb.get('ambiguity_out', 0.3))
        except Exception as e:
            log.debug('feed_predictor error: %s', e)

    def _feed_goal_graph(self, fb: dict) -> None:
        try:
            from goal_graph import GoalGraph
            gg = GoalGraph.get()
            if fb['goal_progress'] > 0.4:
                active = gg.active_goals()
                if active:
                    gg.activate(active[0]['id'])
        except Exception as e:
            log.debug('feed_goal_graph error: %s', e)

    # ── Stats ──────────────────────────────────────────────────────────────────

    def _update_running_stats(self, success: float, novelty: float,
                              friction: float) -> None:
        α = 0.05
        self._mean_success  = self._mean_success  * (1 - α) + success  * α
        self._mean_novelty  = self._mean_novelty  * (1 - α) + novelty  * α
        self._mean_friction = self._mean_friction * (1 - α) + friction * α

    # ── Persistence ────────────────────────────────────────────────────────────

    def _append_to_log(self, fb: dict) -> None:
        try:
            os.makedirs(os.path.dirname(_DATA_PATH), exist_ok=True)
            with open(_DATA_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(fb, ensure_ascii=False) + '\n')
            # Trim to last _ROLLING_MAX lines
            self._trim_log()
        except Exception as e:
            log.debug('feedback log error: %s', e)

    def _trim_log(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                lines = f.readlines()
            if len(lines) > _ROLLING_MAX:
                with open(_DATA_PATH, 'w', encoding='utf-8') as f:
                    f.writelines(lines[-_ROLLING_MAX:])
        except Exception:
            pass

    def _load(self) -> None:
        try:
            with open(_DATA_PATH, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._history.append(json.loads(line))
            self._total = len(self._history)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.debug('feedback load error: %s', e)
