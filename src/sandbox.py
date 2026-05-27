"""
Cognitive Sandbox — Internal Simulation Before Execution
==========================================================
Before real execution, simulate internally.

Pipeline:
    candidate action
        ↓
    predicted semantic outcomes  (EmbeddingIndex k-NN + JAM field projection)
        ↓
    contradiction forecast        (ContradictionRegistry dry-run)
        ↓
    identity impact               (Worldview persistent concept overlap)
        ↓
    execution probability         (weighted composite score)

Without this, runtime is impulsive.
With this, the engine can reject or defer actions that would destabilise identity.

SandboxResult schema:
    {
      action_text:           str    the candidate action
      predicted_outcomes:    list[dict]  [{concept, probability, is_novel}]
      contradiction_risk:    float  [0, 1]  risk of creating new contradictions
      identity_delta:        float  [-1, 1]  positive = strengthens identity
      coherence_delta:       float  [-1, 1]  predicted change in field coherence
      execute_probability:   float  [0, 1]  composite go/no-go score
      sim_trace:             list[str]  reasoning steps
      recommended:           bool   execute_probability > 0.5
    }

Public API:
    CognitiveSandbox.get()
    sandbox.simulate(action_text, concepts, strategy) → SandboxResult
    sandbox.snapshot() → dict
"""

from __future__ import annotations

import time
from typing import Optional

from logger import get_logger

log = get_logger('sandbox')

_SINGLETON: Optional['CognitiveSandbox'] = None

# ── Weights for execute_probability ────────────────────────────────────────────
_W_SUCCESS_PRED    = 0.35
_W_CONTRADICTION   = 0.25   # inverted
_W_IDENTITY        = 0.25
_W_COHERENCE       = 0.15


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ══════════════════════════════════════════════════════════════════════════════
# CognitiveSandbox
# ══════════════════════════════════════════════════════════════════════════════

class CognitiveSandbox:

    def __init__(self) -> None:
        self._sim_count = 0
        self._last_result: Optional[dict] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def simulate(self, action_text: str, concepts: list[str],
                 strategy: str = 'medium') -> dict:
        """
        Run an internal simulation of the candidate action.

        Returns a SandboxResult dict. Does NOT modify any system state.
        """
        t0 = time.monotonic()
        self._sim_count += 1
        trace: list[str] = []

        # ── Step 1: Predict semantic outcomes ─────────────────────────────────
        predicted_outcomes, success_pred = self._predict_outcomes(
            action_text, concepts, strategy, trace
        )

        # ── Step 2: Forecast contradictions ───────────────────────────────────
        contradiction_risk = self._forecast_contradictions(
            predicted_outcomes, concepts, trace
        )

        # ── Step 3: Identity impact ────────────────────────────────────────────
        identity_delta = self._estimate_identity_impact(
            predicted_outcomes, trace
        )

        # ── Step 4: Coherence delta ────────────────────────────────────────────
        coherence_delta = self._estimate_coherence_delta(
            predicted_outcomes, concepts, trace
        )

        # ── Step 5: Execute probability ────────────────────────────────────────
        execute_prob = _clamp(
            success_pred       * _W_SUCCESS_PRED +
            (1.0 - contradiction_risk) * _W_CONTRADICTION +
            (_clamp(identity_delta + 0.5)) * _W_IDENTITY +
            (_clamp(coherence_delta + 0.5)) * _W_COHERENCE
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        trace.append(f'simulation completed in {elapsed_ms}ms')

        result = {
            'action_text':          action_text[:200],
            'predicted_outcomes':   predicted_outcomes[:8],
            'contradiction_risk':   round(contradiction_risk, 3),
            'identity_delta':       round(identity_delta, 3),
            'coherence_delta':      round(coherence_delta, 3),
            'execute_probability':  round(execute_prob, 3),
            'sim_trace':            trace,
            'recommended':          execute_prob > 0.5,
            'sim_ms':               elapsed_ms,
        }

        self._last_result = result
        log.debug('sandbox sim #%d: action="%s..." exec_prob=%.2f recommended=%s',
                  self._sim_count, action_text[:40],
                  execute_prob, result['recommended'])
        return result

    def last_result(self) -> Optional[dict]:
        return self._last_result

    def snapshot(self) -> dict:
        return {
            'sim_count':   self._sim_count,
            'last_result': self._last_result,
        }

    @classmethod
    def get(cls) -> 'CognitiveSandbox':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ── Simulation steps ───────────────────────────────────────────────────────

    def _predict_outcomes(self, action_text: str, concepts: list[str],
                          strategy: str, trace: list[str]) -> tuple[list[dict], float]:
        """
        Embed action_text → k-NN in EmbeddingIndex → project activation
        through JAM field to get predicted activated concepts.
        """
        outcomes = []
        success_pred = 0.5

        try:
            from embed_index import EmbeddingIndex
            from jam_field import JamField

            idx    = EmbeddingIndex.get()
            field  = JamField.get()
            active = field.active(threshold=0.03)

            # Embed the action text directly
            from extractor import extract
            action_concepts = extract(action_text)
            if action_concepts:
                vec = action_concepts[0].embedding
                neighbors = idx.knn(vec, k=12)

                novel_count = 0
                for text, sim in neighbors:
                    current_act = active.get(text, 0.0)
                    predicted_act = _clamp(current_act + sim * 0.3)
                    is_novel = text not in active
                    if is_novel:
                        novel_count += 1
                    outcomes.append({
                        'concept':       text,
                        'probability':   round(sim, 3),
                        'current_act':   round(current_act, 3),
                        'predicted_act': round(predicted_act, 3),
                        'is_novel':      is_novel,
                    })

                novel_ratio = novel_count / max(len(outcomes), 1)
                # High strategy + high novelty = higher success prediction
                strategy_boost = {'low': 0.0, 'medium': 0.1, 'high': 0.2}.get(strategy, 0.1)
                success_pred = _clamp(0.4 + novel_ratio * 0.3 + strategy_boost)
                trace.append(f'predicted {len(outcomes)} outcome concepts ({novel_count} novel)')
            else:
                trace.append('no action concepts extracted — using context concepts')
                # Fall back to concept neighbors
                for c in concepts[:4]:
                    neighbors = idx.knn_text(c, k=4)
                    for text, sim in neighbors:
                        outcomes.append({
                            'concept':    text,
                            'probability': round(sim * 0.7, 3),
                            'is_novel':   text not in active,
                        })

        except Exception as e:
            trace.append(f'outcome prediction error: {e}')
            success_pred = 0.4

        return outcomes, success_pred

    def _forecast_contradictions(self, predicted_outcomes: list[dict],
                                  context_concepts: list[str],
                                  trace: list[str]) -> float:
        risk = 0.0
        try:
            from contradiction import ContradictionRegistry
            predicted_texts = [o['concept'] for o in predicted_outcomes]
            all_concepts    = list(set(context_concepts + predicted_texts))

            # Use _is_bidirectional check for predicted pairs (dry-run)
            cr = ContradictionRegistry.get()
            risk_count = 0
            checks = 0
            for i, a in enumerate(predicted_texts[:6]):
                for b in predicted_texts[i+1:6]:
                    checks += 1
                    if cr._already_registered(a, b):
                        risk_count += 1  # known contradiction will be re-triggered
                    elif cr._is_bidirectional(a, b):
                        risk_count += 1  # new contradiction would be created
            risk = risk_count / max(checks, 1) if checks > 0 else 0.0
            trace.append(f'contradiction forecast: {risk_count}/{checks} pairs at risk (risk={risk:.2f})')
        except Exception as e:
            trace.append(f'contradiction forecast error: {e}')
        return _clamp(risk)

    def _estimate_identity_impact(self, predicted_outcomes: list[dict],
                                   trace: list[str]) -> float:
        delta = 0.0
        try:
            from worldview import Worldview
            wv = Worldview.get()
            snap = wv.snapshot()

            persistent = {c['concept'] for c in snap.get('persistent_concepts', [])}
            predicted_texts = {o['concept'] for o in predicted_outcomes}

            # Positive: predicted outcomes align with persistent identity concepts
            alignment = len(persistent & predicted_texts) / max(len(persistent), 1)

            # Negative: predicted outcomes include chronic contradiction concepts
            chronic_texts: set[str] = set()
            for cc in snap.get('chronic_contradictions', []):
                chronic_texts.add(cc.get('concept_a', ''))
                chronic_texts.add(cc.get('concept_b', ''))
            disruption = len(chronic_texts & predicted_texts) / max(len(chronic_texts), 1)

            delta = alignment * 0.5 - disruption * 0.3
            trace.append(f'identity impact: alignment={alignment:.2f} disruption={disruption:.2f} delta={delta:.2f}')
        except Exception as e:
            trace.append(f'identity impact error: {e}')
        return max(-1.0, min(1.0, delta))

    def _estimate_coherence_delta(self, predicted_outcomes: list[dict],
                                   context_concepts: list[str],
                                   trace: list[str]) -> float:
        delta = 0.0
        try:
            from jam_field import JamField
            field = JamField.get()
            snap  = field.snapshot()
            current_coherence = snap.get('coherence', 0.0)

            # Estimate: if predicted concepts are mostly active in field → coherence grows
            active = field.active(threshold=0.05)
            pred_texts = [o['concept'] for o in predicted_outcomes]
            in_field = sum(1 for c in pred_texts if c in active)
            coverage = in_field / max(len(pred_texts), 1)

            predicted_coherence = _clamp(current_coherence + (coverage - 0.5) * 0.2)
            delta = predicted_coherence - current_coherence
            trace.append(f'coherence delta: {current_coherence:.2f} → {predicted_coherence:.2f} (Δ={delta:.2f})')
        except Exception as e:
            trace.append(f'coherence delta error: {e}')
        return max(-1.0, min(1.0, delta))
