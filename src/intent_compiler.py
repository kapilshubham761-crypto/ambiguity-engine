"""
Intent Compiler — semantic → executable
==========================================
The center of the architecture.

Without it: cognition stays abstract, runtime stays mechanical.
With it: the engine translates its own cognitive state into a structured,
executable plan — complete with strategy, subgoals, uncertainty estimates,
reversibility tags, and a sandbox pre-evaluation.

Pipeline:
    cognitive state (JAM field + regulation + goal graph + worldview)
        ↓
    raw intent extraction      — what does the engine actually want right now?
        ↓
    strategy selection         — how should it pursue that intent?
        ↓
    subgoal decomposition      — what concrete steps does that strategy require?
        ↓
    uncertainty estimation     — how confident is the engine?
        ↓
    reversibility tagging      — which subgoals are safe to undo?
        ↓
    sandbox pre-evaluation     — simulate before committing
        ↓
    CompiledIntent             — structured, executable plan

CompiledIntent schema:
    {
      id:               str    UUID
      ts:               float  unix timestamp
      goal_text:        str    natural language intent
      strategy:         str    explore | consolidate | resolve | expand | stabilise
      subgoals:         list[SubGoal]
      uncertainty:      float  [0, 1]
      reversibility:    float  [0, 1]  fraction of subgoals that are reversible
      sandbox_result:   dict   SandboxResult from CognitiveSandbox
      mode:             str    current cognitive mode
      pressure:         float  current field pressure
      approved:         bool   sandbox recommends execution
    }

SubGoal schema:
    {
      id:          str
      text:        str
      priority:    float  [0, 1]
      reversible:  bool
      depends_on:  list[str]  sibling subgoal ids
    }

Public API:
    IntentCompiler.get()
    compiler.compile(concepts, ambiguity_result) → CompiledIntent
    compiler.last_intent() → Optional[dict]
    compiler.snapshot() → dict
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from logger import get_logger

log = get_logger('intent_compiler')

_SINGLETON: Optional['IntentCompiler'] = None

# ── Strategy templates ─────────────────────────────────────────────────────────
# Maps (mode, goal) → (strategy, subgoal_templates)
_STRATEGY_MAP: dict[tuple[str, str], tuple[str, list[str]]] = {
    # resolve mode
    ('conflicted', 'resolve_tension'): ('resolve', [
        'identify the two concepts in highest semantic conflict',
        'retrieve all edge connections between them from the graph',
        'generate a bridging concept that spans both poles',
        'activate the bridging concept in the field with medium gain',
        'register resolution outcome in contradiction registry',
    ]),
    ('conflicted', 'stabilise'): ('stabilise', [
        'locate the highest-tension nodes in the field',
        'reduce diffusion around conflict zones',
        'reinforce stable concept clusters as anchors',
    ]),
    # explore mode
    ('exploratory', 'explore'): ('explore', [
        'select the lowest-coherence region of the field',
        'fetch concepts from adjacent unexplored domains',
        'embed new concepts and measure novelty against existing graph',
        'ingest high-novelty concepts with boosted gain',
    ]),
    ('exploratory', 'expand_knowledge'): ('expand', [
        'identify knowledge frontier concepts (high novelty, low persistence)',
        'search for bridge sources connecting frontier to core',
        'extract and embed new semantic relationships',
        'update world model with new causal edges',
    ]),
    # consolidate mode
    ('focused', 'consolidate'): ('consolidate', [
        'select top 20 high-persistence concepts',
        'run abstractor clustering on co-occurring pairs',
        'strengthen edges between cluster members',
        'decay peripheral low-activation nodes',
    ]),
    ('saturated', 'stabilise'): ('stabilise', [
        'increase decay rate to clear stale activations',
        'reduce diffusion strength to prevent runaway propagation',
        'anchor top-3 most stable concepts as field seeds',
    ]),
    # drifting mode
    ('drifting', 'expand_knowledge'): ('expand', [
        'identify concepts with highest momentum but low stability',
        'anchor drifting concepts to persistent graph nodes',
        'reinforce identity-core concepts from worldview',
    ]),
}

_DEFAULT_STRATEGY = ('explore', [
    'survey current field activation landscape',
    'identify highest-novelty concepts',
    'fetch and embed new content on top topic',
    'update field with ingested concepts',
])

# Reversibility heuristic — certain action types are reversible
_REVERSIBLE_KEYWORDS = {'identify', 'survey', 'retrieve', 'measure', 'estimate',
                        'locate', 'select', 'fetch', 'read', 'check', 'compute'}
_IRREVERSIBLE_KEYWORDS = {'decay', 'dissolve', 'delete', 'remove', 'overwrite',
                          'register', 'update', 'write', 'ingest', 'strengthen'}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _is_reversible(text: str) -> bool:
    words = set(text.lower().split())
    if words & _IRREVERSIBLE_KEYWORDS:
        return False
    if words & _REVERSIBLE_KEYWORDS:
        return True
    return True  # default: assume reversible if ambiguous


# ══════════════════════════════════════════════════════════════════════════════
# IntentCompiler
# ══════════════════════════════════════════════════════════════════════════════

class IntentCompiler:

    def __init__(self) -> None:
        self._compile_count  = 0
        self._last_intent: Optional[dict] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def compile(self, concepts: list[str],
                ambiguity_result: Optional[dict] = None) -> dict:
        """
        Compile the current cognitive state into a structured executable intent.

        concepts         — concept texts from current cycle
        ambiguity_result — AmbiguityResult dict from detector (optional)
        """
        t0 = time.monotonic()
        self._compile_count += 1

        # ── 1. Read cognitive state ────────────────────────────────────────────
        mode, goal_str, pressure, entropy = self._read_regulation()
        top_goals = self._read_goal_graph()
        worldview_summary = self._read_worldview()
        field_snap = self._read_field()

        # ── 2. Extract intent from state ───────────────────────────────────────
        goal_text = self._extract_intent(top_goals, goal_str, mode, concepts)

        # ── 3. Select strategy ─────────────────────────────────────────────────
        strategy, subgoal_templates = self._select_strategy(mode, goal_str)

        # ── 4. Decompose into subgoals ─────────────────────────────────────────
        subgoals = self._decompose(subgoal_templates, concepts)

        # ── 5. Estimate uncertainty ────────────────────────────────────────────
        uncertainty = self._estimate_uncertainty(ambiguity_result, pressure, entropy)

        # ── 6. Reversibility ───────────────────────────────────────────────────
        reversible_count = sum(1 for sg in subgoals if sg['reversible'])
        reversibility = reversible_count / max(len(subgoals), 1)

        # ── 7. Sandbox pre-evaluation ──────────────────────────────────────────
        sandbox_result = self._run_sandbox(goal_text, concepts, strategy)

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        intent = {
            'id':             str(uuid.uuid4()),
            'ts':             time.time(),
            'goal_text':      goal_text,
            'strategy':       strategy,
            'subgoals':       subgoals,
            'uncertainty':    round(uncertainty, 3),
            'reversibility':  round(reversibility, 3),
            'sandbox_result': sandbox_result,
            'mode':           mode,
            'goal':           goal_str,
            'pressure':       round(pressure, 3),
            'entropy':        round(entropy, 3),
            'approved':       sandbox_result.get('recommended', True),
            'compile_ms':     elapsed_ms,
            'worldview_hint': worldview_summary,
            'concept_count':  len(concepts),
        }

        self._last_intent = intent
        log.debug(
            'intent compiled #%d: strategy=%s uncertainty=%.2f approved=%s (%dms)',
            self._compile_count, strategy, uncertainty,
            intent['approved'], elapsed_ms
        )
        return intent

    def last_intent(self) -> Optional[dict]:
        return self._last_intent

    def snapshot(self) -> dict:
        return {
            'compile_count': self._compile_count,
            'last_intent':   self._last_intent,
        }

    @classmethod
    def get(cls) -> 'IntentCompiler':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ── State readers ──────────────────────────────────────────────────────────

    def _read_regulation(self) -> tuple[str, str, float, float]:
        try:
            from regulation import Regulation
            snap = Regulation.get().snapshot()
            return (
                snap.get('mode', 'associative'),
                snap.get('goal', 'explore'),
                float(snap.get('pressure', 0.3)),
                float(snap.get('entropy', 0.5)),
            )
        except Exception:
            return 'associative', 'explore', 0.3, 0.5

    def _read_goal_graph(self) -> list[dict]:
        try:
            from goal_graph import GoalGraph
            return GoalGraph.get().top(5)
        except Exception:
            return []

    def _read_worldview(self) -> str:
        try:
            from worldview import Worldview
            return Worldview.get().identity_summary()
        except Exception:
            return ''

    def _read_field(self) -> dict:
        try:
            from jam_field import JamField
            return JamField.get().snapshot()
        except Exception:
            return {}

    # ── Compilation steps ──────────────────────────────────────────────────────

    def _extract_intent(self, top_goals: list[dict], goal_str: str,
                        mode: str, concepts: list[str]) -> str:
        if top_goals:
            top = top_goals[0]
            weight = top.get('weight', 0.5)
            pressure = top.get('emotional_pressure', 0.5)
            return (
                f"{top['text']} "
                f"[weight={weight:.2f} pressure={pressure:.2f} mode={mode}]"
            )
        # Fallback: construct from regulation goal + top concepts
        top_c = ', '.join(concepts[:3]) if concepts else 'current context'
        goal_text_map = {
            'resolve_tension':  f'resolve semantic tension around: {top_c}',
            'expand_knowledge': f'expand knowledge into: {top_c}',
            'consolidate':      f'consolidate understanding of: {top_c}',
            'stabilise':        f'stabilise activation around: {top_c}',
            'explore':          f'explore concepts related to: {top_c}',
        }
        return goal_text_map.get(goal_str, f'process: {top_c}')

    def _select_strategy(self, mode: str,
                         goal_str: str) -> tuple[str, list[str]]:
        key = (mode, goal_str)
        if key in _STRATEGY_MAP:
            return _STRATEGY_MAP[key]
        # Partial match on mode only
        for (m, g), val in _STRATEGY_MAP.items():
            if m == mode:
                return val
        # Partial match on goal only
        for (m, g), val in _STRATEGY_MAP.items():
            if g == goal_str:
                return val
        return _DEFAULT_STRATEGY

    def _decompose(self, templates: list[str],
                   concepts: list[str]) -> list[dict]:
        subgoals = []
        parent_id = None
        for i, template in enumerate(templates):
            sg_id = str(uuid.uuid4())[:8]
            # Inject top concept into template if placeholder available
            text = template
            if concepts and '{concept}' in template:
                text = template.replace('{concept}', concepts[0])

            reversible = _is_reversible(text)
            sg = {
                'id':         sg_id,
                'text':       text,
                'priority':   round(1.0 - i * 0.15, 2),
                'reversible': reversible,
                'depends_on': [parent_id] if parent_id and i > 0 else [],
            }
            subgoals.append(sg)
            parent_id = sg_id
        return subgoals

    def _estimate_uncertainty(self, ambiguity_result: Optional[dict],
                               pressure: float, entropy: float) -> float:
        base = 0.3
        if ambiguity_result:
            score = float(ambiguity_result.get('score', 0.3))
            base = score * 0.6 + pressure * 0.2 + entropy * 0.2
        else:
            base = pressure * 0.4 + entropy * 0.4 + 0.2
        # Check contradiction density
        try:
            from contradiction import ContradictionRegistry
            n_open = len(ContradictionRegistry.get().unresolved())
            base += _clamp(n_open / 50.0) * 0.15
        except Exception:
            pass
        return _clamp(base)

    def _run_sandbox(self, goal_text: str, concepts: list[str],
                     strategy: str) -> dict:
        try:
            from sandbox import CognitiveSandbox
            return CognitiveSandbox.get().simulate(goal_text, concepts, strategy)
        except Exception as e:
            log.debug('sandbox error: %s', e)
            return {
                'recommended': True,
                'execute_probability': 0.6,
                'sim_trace': [f'sandbox unavailable: {e}'],
            }
