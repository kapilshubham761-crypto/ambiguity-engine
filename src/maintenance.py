"""
Phase 6: Decay & Pruning.

6.1  Temporal decay   — edge weights × 0.99 per day since last_updated
6.2  Weak edge pruning — drop edges whose weight has fallen below 0.1
6.3  Orphan pruning   — remove nodes that have had no edges for ≥ 14 days
6.5  Daily gate       — the whole routine runs at most once per calendar day
     (checked against a stamp file; call run_maintenance() on every startup)
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

from logger import get_logger

log = get_logger('maintenance')

_STAMP_PATH   = os.path.join(os.path.dirname(__file__), '..', 'data', 'last_cleaned.txt')
DECAY_FACTOR  = 0.99    # 6.1 — per day
PRUNE_WEIGHT  = 0.10    # 6.2 — drop edges below this
ORPHAN_DAYS   = 14      # 6.3 — days without edges before node is removed


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _days_since(iso_str: str) -> float:
    """Return fractional days between an ISO timestamp and now (UTC)."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - dt
        return delta.total_seconds() / 86_400
    except Exception:
        return 0.0


def _last_cleaned() -> date | None:
    try:
        text = open(_STAMP_PATH).read().strip()
        return date.fromisoformat(text)
    except Exception:
        return None


def _mark_cleaned() -> None:
    os.makedirs(os.path.dirname(_STAMP_PATH), exist_ok=True)
    with open(_STAMP_PATH, 'w') as f:
        f.write(date.today().isoformat())


# --------------------------------------------------------------------------- #
# Core routines                                                                #
# --------------------------------------------------------------------------- #

def apply_decay(graph, decay_factor: float = DECAY_FACTOR) -> int:
    """
    6.1 — Multiply each edge weight by decay_factor ^ days_since_last_update.
    Returns the number of edges that were decayed.
    """
    decayed = 0
    for u, v, data in list(graph._g.edges(data=True)):
        days = _days_since(data.get('last_updated', datetime.utcnow().isoformat()))
        if days < 1:
            continue
        factor = decay_factor ** days
        old_w  = data['weight']
        new_w  = old_w * factor
        graph._g[u][v]['weight'] = new_w
        decayed += 1
        log.debug('Decayed edge %s–%s: %.4f → %.4f (%.1f days)', u, v, old_w, new_w, days)
    return decayed


def prune_weak_edges(graph, prune_weight: float = PRUNE_WEIGHT) -> int:
    """
    6.2 — Remove edges whose weight is below prune_weight.
    Returns the number of edges removed.
    """
    to_remove = [
        (u, v)
        for u, v, data in graph._g.edges(data=True)
        if data['weight'] < prune_weight
    ]
    for u, v in to_remove:
        graph._g.remove_edge(u, v)
        log.debug('Pruned weak edge %s–%s', u, v)
    return len(to_remove)


def prune_orphan_nodes(graph, orphan_days: int = ORPHAN_DAYS) -> int:
    """
    6.3 — Remove nodes that currently have no edges AND whose last_seen
    timestamp is more than orphan_days old.
    Returns the number of nodes removed.
    """
    to_remove = []
    for nid, data in list(graph._g.nodes(data=True)):
        if graph._g.degree(nid) > 0:
            continue
        days = _days_since(data.get('last_seen', datetime.utcnow().isoformat()))
        if days >= orphan_days:
            to_remove.append(nid)
            log.debug('Orphan node "%s" last seen %.1f days ago — removing', data['text'], days)

    for nid in to_remove:
        graph._g.remove_node(nid)
    return len(to_remove)


# --------------------------------------------------------------------------- #
# Daily gate (6.5)                                                             #
# --------------------------------------------------------------------------- #

def preview_maintenance(graph, decay_factor: float = DECAY_FACTOR,
                        prune_weight: float = PRUNE_WEIGHT,
                        orphan_days: int = ORPHAN_DAYS) -> dict:
    """
    Dry-run: return what *would* be deleted/decayed without modifying the graph.
    Returns dict with lists of affected edges and nodes.
    """
    edges_to_decay = []
    edges_to_prune = []
    nodes_to_prune = []

    for u, v, data in graph._g.edges(data=True):
        days  = _days_since(data.get('last_updated', datetime.utcnow().isoformat()))
        old_w = data['weight']
        if days >= 1:
            new_w = old_w * (decay_factor ** days)
            edges_to_decay.append({
                'concept_a': graph._g.nodes[u].get('text', u),
                'concept_b': graph._g.nodes[v].get('text', v),
                'weight_before': round(old_w, 4),
                'weight_after':  round(new_w, 4),
                'days_old':      round(days, 1),
                'will_prune':    new_w < prune_weight,
            })
        else:
            if old_w < prune_weight:
                edges_to_prune.append({
                    'concept_a': graph._g.nodes[u].get('text', u),
                    'concept_b': graph._g.nodes[v].get('text', v),
                    'weight': round(old_w, 4),
                    'days_old': round(days, 1),
                })

    for nid, data in graph._g.nodes(data=True):
        if graph._g.degree(nid) > 0:
            continue
        days = _days_since(data.get('last_seen', datetime.utcnow().isoformat()))
        if days >= orphan_days:
            nodes_to_prune.append({
                'concept':  data.get('text', nid),
                'last_seen': data.get('last_seen', '')[:10],
                'days_idle': round(days, 1),
            })

    return {
        'edges_to_decay': edges_to_decay,
        'edges_to_prune': edges_to_prune,
        'nodes_to_prune': nodes_to_prune,
    }


def run_maintenance(graph, force: bool = False,
                    decay_factor: float = DECAY_FACTOR,
                    prune_weight: float = PRUNE_WEIGHT,
                    orphan_days:  int   = ORPHAN_DAYS) -> dict:
    """
    6.5 — Run the full maintenance cycle at most once per calendar day.
    Pass force=True to bypass the daily gate (used in tests).

    Returns a summary dict with counts of what changed.
    """
    today = date.today()
    last  = _last_cleaned()

    if not force and last == today:
        log.debug('Maintenance already ran today (%s) — skipping.', today)
        return {'skipped': True, 'date': today.isoformat()}

    nodes_before = graph.node_count
    edges_before = graph.edge_count

    decayed  = apply_decay(graph, decay_factor)
    pruned_e = prune_weak_edges(graph, prune_weight)
    pruned_n = prune_orphan_nodes(graph, orphan_days)

    nodes_after = graph.node_count
    edges_after = graph.edge_count

    summary = {
        'skipped':       False,
        'date':          today.isoformat(),
        'edges_decayed': decayed,
        'edges_pruned':  pruned_e,
        'nodes_pruned':  pruned_n,
        'nodes_before':  nodes_before,
        'nodes_after':   nodes_after,
        'edges_before':  edges_before,
        'edges_after':   edges_after,
    }

    _mark_cleaned()
    graph.save()

    log.info(
        'Maintenance done: decayed=%d  edges_pruned=%d  nodes_pruned=%d  '
        '(%d→%d nodes, %d→%d edges)',
        decayed, pruned_e, pruned_n,
        nodes_before, nodes_after,
        edges_before, edges_after,
    )
    return summary
