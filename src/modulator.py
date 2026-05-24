"""
Modulation Layer.

Translates an AmbiguityResult + SemanticGraph into a system prompt that
shapes how the LLM responds. Three regimes:

  low    → clean, direct system prompt, no graph injection
  medium → inject 3-5 highest-weighted graph neighbours as context
  high   → explicit tension framing + up to 8 neighbours + meta-state pressure

Provides A/B logging: run the same input with and without modulation
and store both outputs side-by-side in logs/ab_log.jsonl.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

import requests

from logger import get_logger

log = get_logger('modulator')

_AB_LOG = os.path.join(os.path.dirname(__file__), '..', 'logs', 'ab_log.jsonl')

# ── Prompt templates ──────────────────────────────────────────────────────────

_LOW_PROMPT = """\
You are a clear, direct assistant. Answer the user's question concisely and accurately."""

_MEDIUM_TEMPLATE = """\
You are a thoughtful assistant with access to a semantic memory.
Related concepts from prior context: {neighbours}
Use this background where relevant, but stay focused on the user's question."""

_HIGH_TEMPLATE = """\
You are a careful, exploratory assistant. The user's input spans concepts that \
pull in different directions: {concept_list}.
These tensions are worth acknowledging. Before converging on an answer, briefly \
map the conceptual territory — where do these ideas agree, where do they diverge, \
and what sits at the bridge between them?
Related concepts from prior context: {neighbours}
Currently active pressure concepts: {meta_concepts}"""


# ── Neighbour retrieval ───────────────────────────────────────────────────────

def _get_neighbours(concepts: list, graph, top_k: int) -> list[str]:
    if graph is None or graph.node_count == 0:
        return []

    import numpy as np

    node_ids  = list(graph._g.nodes())
    node_embs = {nid: graph._g.nodes[nid]['embedding'] for nid in node_ids}
    all_embs  = np.array([node_embs[nid] for nid in node_ids], dtype=np.float32)
    input_texts = {c.text for c in concepts}
    scored: dict[str, float] = {}

    for concept in concepts:
        emb = np.array(concept.embedding, dtype=np.float32)
        norm = np.linalg.norm(emb)
        if norm == 0:
            continue
        sims = all_embs @ emb / (np.linalg.norm(all_embs, axis=1) * norm + 1e-8)
        closest_id = node_ids[int(np.argmax(sims))]

        for nb in graph._g.neighbors(closest_id):
            nb_text   = graph._g.nodes[nb]['text']
            nb_weight = graph._g[closest_id][nb]['weight']
            if nb_text in input_texts:
                continue
            if nb_text not in scored or scored[nb_text] < nb_weight:
                scored[nb_text] = nb_weight

    ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    return [text for text, _ in ranked[:top_k]]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ModulationResult:
    level:         str
    system_prompt: str
    neighbours:    list[str]
    concept_list:  list[str]
    meta_concepts: list[str] = field(default_factory=list)


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(concepts: list, result, graph=None,
                 medium_k: int = 5, high_k: int = 8,
                 meta=None) -> ModulationResult:
    level        = result.level
    concept_list = [c.text for c in concepts]

    meta_concepts: list[str] = []
    if meta is not None:
        try:
            meta_concepts = [t for t, _ in meta.top(n=5)]
        except Exception:
            pass

    if level == 'low':
        return ModulationResult(
            level='low', system_prompt=_LOW_PROMPT,
            neighbours=[], concept_list=concept_list, meta_concepts=meta_concepts,
        )

    k          = medium_k if level == 'medium' else high_k
    neighbours = _get_neighbours(concepts, graph, top_k=k)
    nb_str     = ', '.join(neighbours) if neighbours else 'none yet'
    mc_str     = ', '.join(meta_concepts) if meta_concepts else 'none'

    if level == 'medium':
        prompt = _MEDIUM_TEMPLATE.format(neighbours=nb_str)
    else:
        prompt = _HIGH_TEMPLATE.format(
            concept_list=', '.join(concept_list),
            neighbours=nb_str,
            meta_concepts=mc_str,
        )

    return ModulationResult(
        level=level, system_prompt=prompt,
        neighbours=neighbours, concept_list=concept_list, meta_concepts=meta_concepts,
    )


# ── LLM call ─────────────────────────────────────────────────────────────────

def call_llm(user_input: str, system_prompt: str, cfg: dict) -> str:
    full_prompt = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_input}"
    payload = {
        'model':  cfg['model']['name'],
        'prompt': full_prompt,
        'stream': cfg['model'].get('stream', False),
    }
    resp = requests.post(cfg['model']['endpoint'], json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()['response'].strip()


# ── A/B logging ───────────────────────────────────────────────────────────────

def run_ab(user_input: str, concepts: list, ambiguity_result,
           graph, cfg: dict, meta=None) -> dict:
    modulated     = build_prompt(concepts, ambiguity_result, graph=graph, meta=meta)
    mod_response  = call_llm(user_input, modulated.system_prompt, cfg)
    ctrl_response = call_llm(user_input, _LOW_PROMPT, cfg)

    entry = {
        'timestamp':        datetime.utcnow().isoformat(timespec='seconds'),
        'input':            user_input,
        'ambiguity_score':  ambiguity_result.score,
        'ambiguity_level':  ambiguity_result.level,
        'concepts':         modulated.concept_list,
        'neighbours':       modulated.neighbours,
        'meta_concepts':    modulated.meta_concepts,
        'system_prompt':    modulated.system_prompt,
        'modulated_output': mod_response,
        'control_output':   ctrl_response,
    }

    os.makedirs(os.path.dirname(_AB_LOG), exist_ok=True)
    with open(_AB_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return entry
