"""
Node [I] — Assessor
==================
Periodic assessment of the semantic graph's cognitive health.

Runs every 3 hours (called by Teacher.check_in()).
Grades five dimensions, saves a timestamped ledger, tracks progress since birth.

Public API
----------
Assessment      dataclass   full report card (grades + metadata)
assess()        run a fresh assessment and return an Assessment
save_card()     append an Assessment to the ledger
load_cards()    load all saved report cards
ensure_birthdate() create data/birthdate.txt on first run
BIRTHDATE       str         ISO date string (e.g. '2026-05-22')
STAGE_TARGETS   dict        imported from curriculum — min nodes/edges per stage
"""

from __future__ import annotations

import json
import os
from datetime import datetime, date, timezone
from dataclasses import dataclass, asdict

from curriculum import STAGE_TARGETS

_ROOT          = os.path.join(os.path.dirname(__file__), '..')
_RC_PATH       = os.path.join(_ROOT, 'data', 'report_cards.json')
_BIRTH_PATH    = os.path.join(_ROOT, 'data', 'birthdate.txt')

BIRTHDATE = '2026-05-22'


# ======================================================================== #
# Grading helpers                                                           #
# ======================================================================== #

def _pct_to_grade(pct: float) -> str:
    if pct >= 0.90: return 'A+'
    if pct >= 0.80: return 'A'
    if pct >= 0.70: return 'B+'
    if pct >= 0.60: return 'B'
    if pct >= 0.50: return 'C+'
    if pct >= 0.40: return 'C'
    if pct >= 0.30: return 'D'
    return 'F'

def _grade_to_gpa(g: str) -> float:
    return {'A+': 4.3, 'A': 4.0, 'B+': 3.3, 'B': 3.0,
            'C+': 2.3, 'C': 2.0, 'D': 1.0, 'F': 0.0}.get(g, 0.0)


# ======================================================================== #
# Assessment dataclass                                                      #
# ======================================================================== #

@dataclass
class Assessment:
    timestamp:       str
    age_days:        int
    stage:           int
    stage_label:     str

    # Raw metrics
    node_count:      int
    edge_count:      int
    avg_degree:      float
    avg_weight:      float
    avg_activation:  float
    ambiguity_high_ratio: float   # fraction of recent scores that were 'high'
    ambiguity_spread:     float   # std-dev of recent scores (calibration quality)

    # Grades (letter)
    grade_breadth:   str    # how many concepts known vs target
    grade_depth:     str    # edge density and weight
    grade_activation:str    # how actively concepts are being reinforced
    grade_calibration: str  # ambiguity scores spread across range (not stuck)
    grade_velocity:  str    # growth since last check-in

    overall_grade:   str
    gpa:             float
    narrative:       str    # one-sentence teacher comment


def _days_since_birth() -> int:
    try:
        bd = date.fromisoformat(BIRTHDATE)
        return (date.today() - bd).days
    except Exception:
        return 0


# ======================================================================== #
# Core assessment logic                                                     #
# ======================================================================== #

def assess(graph, stage: int, prev: Assessment | None = None) -> Assessment:
    """Run a full assessment of the graph. prev = last report card for velocity."""
    import networkx as nx
    import numpy as np

    ts       = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')
    age_days = _days_since_birth()
    target   = STAGE_TARGETS.get(stage, STAGE_TARGETS[7])

    # --- Breadth ---
    node_pct  = min(graph.node_count / max(target['nodes'], 1), 1.0)
    g_breadth = _pct_to_grade(node_pct)

    # --- Depth ---
    degrees  = [d for _, d in graph._g.degree()]
    weights  = [data['weight'] for _, _, data in graph._g.edges(data=True)]
    avg_deg  = float(np.mean(degrees)) if degrees else 0.0
    avg_w    = float(np.mean(weights)) if weights else 0.0
    edge_pct = min(graph.edge_count / max(target['edges'], 1), 1.0)
    depth_pct = (edge_pct + min(avg_w / 0.5, 1.0)) / 2
    g_depth  = _pct_to_grade(depth_pct)

    # --- Activation (reinforcement) ---
    acts = [data.get('activation_count', 0) for _, data in graph._g.nodes(data=True)]
    avg_act = float(np.mean(acts)) if acts else 0.0
    act_pct = min(avg_act / 5.0, 1.0)   # 5+ avg activations = full marks
    g_activation = _pct_to_grade(act_pct)

    # --- Calibration (ambiguity score quality) ---
    score_log = os.path.join(_ROOT, 'logs', 'ambiguity_scores.jsonl')
    scores, levels = [], []
    if os.path.exists(score_log):
        for line in open(score_log, encoding='utf-8'):
            try:
                e = json.loads(line)
                scores.append(e.get('score', 0))
                levels.append(e.get('level', 'low'))
            except Exception:
                pass
    recent_scores  = scores[-100:]
    recent_levels  = levels[-100:]
    high_ratio     = recent_levels.count('high') / max(len(recent_levels), 1)
    spread         = float(np.std(recent_scores)) if len(recent_scores) > 1 else 0.0
    # Good calibration = spread > 0.15 (not all stuck at same value)
    cal_pct        = min(spread / 0.20, 1.0)
    g_cal          = _pct_to_grade(cal_pct)

    # --- Velocity (growth since last check) ---
    if prev is not None:
        node_growth = graph.node_count - prev.node_count
        edge_growth = graph.edge_count - prev.edge_count
        vel_pct = min((node_growth / 20 + edge_growth / 30) / 2, 1.0)
        vel_pct = max(vel_pct, 0.0)
    else:
        vel_pct = node_pct  # first check — use breadth as proxy
    g_vel = _pct_to_grade(vel_pct)

    # --- Overall ---
    grades   = [g_breadth, g_depth, g_activation, g_cal, g_vel]
    gpas     = [_grade_to_gpa(g) for g in grades]
    avg_gpa  = float(np.mean(gpas))
    overall  = _pct_to_grade(avg_gpa / 4.3)

    # --- Narrative ---
    narrative = _narrative(overall, g_breadth, g_depth, g_activation, g_cal, g_vel,
                           graph.node_count, target['nodes'], age_days)

    return Assessment(
        timestamp=ts, age_days=age_days, stage=stage,
        stage_label=target['label'],
        node_count=graph.node_count, edge_count=graph.edge_count,
        avg_degree=round(avg_deg, 2), avg_weight=round(avg_w, 4),
        avg_activation=round(avg_act, 2),
        ambiguity_high_ratio=round(high_ratio, 3),
        ambiguity_spread=round(spread, 4),
        grade_breadth=g_breadth, grade_depth=g_depth,
        grade_activation=g_activation, grade_calibration=g_cal,
        grade_velocity=g_vel,
        overall_grade=overall, gpa=round(avg_gpa, 2),
        narrative=narrative,
    )


def _narrative(overall, breadth, depth, act, cal, vel,
               nodes, target_nodes, age_days) -> str:
    if overall in ('A+', 'A'):
        return f"Excellent progress — {nodes} concepts mastered at day {age_days}. Keep feeding diverse content."
    if overall == 'B+':
        return f"Strong learner. Focus on reinforcing existing concepts to deepen connections."
    if overall == 'B':
        return f"Good foundation with {nodes} concepts. Breadth is {breadth}, depth is {depth} — push harder."
    if overall in ('C+', 'C'):
        return f"Developing steadily. Still {target_nodes - nodes} concepts short of stage target. Accept more lessons."
    if overall == 'D':
        return f"Falling behind. Only {nodes}/{target_nodes} concepts for this stage. Needs intensive feeding."
    return f"Critical — barely any learning detected. Check that content is being accepted and fed into the graph."


# ======================================================================== #
# Persistence                                                               #
# ======================================================================== #

def load_cards() -> list[dict]:
    try:
        with open(_RC_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_card(a: Assessment) -> None:
    cards = load_cards()
    cards.append(asdict(a))
    with open(_RC_PATH, 'w', encoding='utf-8') as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)


def ensure_birthdate() -> str:
    os.makedirs(os.path.dirname(_BIRTH_PATH), exist_ok=True)
    if not os.path.exists(_BIRTH_PATH):
        with open(_BIRTH_PATH, 'w') as f:
            f.write(BIRTHDATE)
    return open(_BIRTH_PATH).read().strip()
