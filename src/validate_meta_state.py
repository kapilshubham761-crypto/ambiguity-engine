"""
Validation — meta_state.py [M]

Covers the three cases from the Step 01 tracker:
  1. reinforce raises the activation score
  2. decay over simulated time
  3. cap drops the weakest entries
"""

import os, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone, timedelta
from meta_state import MetaState

PASS = '✅'
FAIL = '❌'

def _ms(tmp_path: str) -> MetaState:
    """Fresh MetaState backed by a temp file."""
    ms = MetaState(path=tmp_path)
    ms._entries = {}        # ensure clean slate regardless of leftover file
    return ms

results = []

# ─────────────────────────────────────────────────────────────────────────────
# Case 1 — reinforce raises the activation score
# ─────────────────────────────────────────────────────────────────────────────
with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
    tmp = f.name

ms = _ms(tmp)
ms.reinforce(['gravity', 'mass', 'orbit'])
snap = ms.active(threshold=0.0)

ok = all(snap.get(c, 0.0) > 0.0 for c in ['gravity', 'mass', 'orbit'])
results.append((PASS if ok else FAIL, 'reinforce raises activation above 0'))

# second reinforce stacks
before = snap['gravity']
ms.reinforce(['gravity'])
after  = ms.active(threshold=0.0)['gravity']
ok2    = after > before
results.append((PASS if ok2 else FAIL, f'double-reinforce stacks: {before:.3f} → {after:.3f}'))

# clamp at 1.0
for _ in range(20):
    ms.reinforce(['gravity'])
capped = ms.active(threshold=0.0).get('gravity', 0.0)
ok3    = capped <= 1.0
results.append((PASS if ok3 else FAIL, f'activation clamped at 1.0 (got {capped:.4f})'))

# ─────────────────────────────────────────────────────────────────────────────
# Case 2 — decay over simulated time
# ─────────────────────────────────────────────────────────────────────────────
ms2 = _ms(tmp)
ms2.reinforce(['quantum'])
raw_val   = ms2._entries['quantum']['value']

# simulate 60 minutes elapsed
future = _now_dt = datetime.now(tz=timezone.utc) + timedelta(minutes=60)
decayed_val = ms2._decayed_value('quantum', future)
expected_lo = raw_val * (0.97 ** 60) - 0.01   # allow small float tolerance
expected_hi = raw_val * (0.97 ** 60) + 0.01

ok4 = expected_lo <= decayed_val <= expected_hi
results.append((PASS if ok4 else FAIL,
    f'decay 60 min: {raw_val:.4f} → {decayed_val:.4f} '
    f'(expected ≈ {raw_val*(0.97**60):.4f})'))

# decay_to() prunes entries that dropped below 0.001
ms2._entries['ancient'] = {
    'value': 0.0005,
    'stored_at': datetime.now(tz=timezone.utc).isoformat(),
}
ms2.decay_to(datetime.now(tz=timezone.utc))
ok5 = 'ancient' not in ms2._entries
results.append((PASS if ok5 else FAIL, 'decay_to() prunes entries below 0.001'))

# concepts with high activation survive decay
ms2.reinforce(['light'])
ms2.decay_to(datetime.now(tz=timezone.utc) + timedelta(minutes=5))
ok6 = 'light' in ms2._entries
results.append((PASS if ok6 else FAIL, 'high-activation concept survives 5-min decay'))

# ─────────────────────────────────────────────────────────────────────────────
# Case 3 — cap drops the weakest entries
# ─────────────────────────────────────────────────────────────────────────────
ms3 = _ms(tmp)
ms3._max_concepts = 5

# Add 8 concepts with varying strength (reinforce different counts)
strong = ['alpha', 'beta', 'gamma']
weak   = ['x1', 'x2', 'x3', 'x4', 'x5']

for _ in range(4):
    ms3.reinforce(strong)
ms3.reinforce(weak)

ok7 = len(ms3._entries) <= 5
results.append((PASS if ok7 else FAIL,
    f'cap enforced: pool = {len(ms3._entries)} ≤ 5'))

# strong concepts survive the cap cull
all_strong_present = all(c in ms3._entries for c in strong)
results.append((PASS if all_strong_present else FAIL,
    'strongest entries survive cap cull'))

# top() respects n
top3 = ms3.top(3)
ok8  = len(top3) <= 3
results.append((PASS if ok8 else FAIL, f'top(3) returns ≤ 3 items (got {len(top3)})'))

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
os.unlink(tmp)

print('\n── validate_meta_state.py ──────────────────────────────')
for mark, label in results:
    print(f'  {mark}  {label}')

total  = len(results)
passed = sum(1 for m, _ in results if m == PASS)
print(f'\n  {passed}/{total} passed')
if passed < total:
    sys.exit(1)
