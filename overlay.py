"""
Ambiguity Engine — always-on-top stats overlay.
Run: python overlay.py
Drag to reposition. Double-click to toggle compact/full mode.
"""

import json
import os
import sys
import time
import tkinter as tk
from datetime import datetime, timezone

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_ROOT, 'data')

# ------------------------------------------------------------------ #
# Colour palette                                                       #
# ------------------------------------------------------------------ #
BG        = '#0d0d0d'
FG        = '#cccccc'
DIM       = '#555555'
ACCENT    = '#4a9eff'
GREEN     = '#44ff88'
RED       = '#ff4444'
ORANGE    = '#ffaa44'
PURPLE    = '#bb88ff'
FONT_MONO = ('Consolas', 9)
FONT_LG   = ('Consolas', 11, 'bold')
FONT_SM   = ('Consolas', 8)

STATUS_COLOURS = {
    'fetching':  ACCENT,
    'learning':  GREEN,
    'filtering': ORANGE,
    'assessing': PURPLE,
    'paused':    RED,
    'idle':      DIM,
}


# ------------------------------------------------------------------ #
# Data readers                                                         #
# ------------------------------------------------------------------ #

def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _engine_status():
    try:
        if _read(os.path.join(_DATA, 'paused.txt')) is None:
            raw = open(os.path.join(_DATA, 'paused.txt')).read().strip()
            if raw == '1':
                return 'paused'
    except Exception:
        pass
    try:
        with open(os.path.join(_DATA, 'paused.txt')) as f:
            if f.read().strip() == '1':
                return 'paused'
    except Exception:
        pass

    q = _read(os.path.join(_DATA, 'teacher_queue.json'))
    queue_size = len(q) if isinstance(q, list) else -1

    stats = _read(os.path.join(_DATA, 'teacher_stats.json'))
    if stats:
        sessions = stats.get('sessions', [])
        if sessions:
            last = sessions[-1]
            try:
                last_dt = datetime.fromisoformat(last['ts'])
                age = (datetime.now(tz=timezone.utc) - last_dt).total_seconds()
                if age < 300:
                    return 'learning' if last['action'] == 'accept' else 'filtering'
            except Exception:
                pass

    cards = _read(os.path.join(_DATA, 'report_cards.json'))
    if cards:
        try:
            last_dt = datetime.fromisoformat(cards[-1]['timestamp'])
            age = (datetime.now(tz=timezone.utc) - last_dt).total_seconds()
            if age < 120:
                return 'assessing'
        except Exception:
            pass

    fs = _read(os.path.join(_DATA, 'fetch_status.json'))
    if fs and fs.get('fetching'):
        return 'fetching'

    if queue_size == 0 or (0 < queue_size < 4):
        return 'fetching'

    return 'idle'


def _fetch_elapsed():
    fs = _read(os.path.join(_DATA, 'fetch_status.json'))
    if fs and fs.get('fetching') and fs.get('started_at'):
        try:
            started = datetime.fromisoformat(fs['started_at'])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            secs = int((datetime.now(tz=timezone.utc) - started).total_seconds())
            m, s = divmod(secs, 60)
            return f"{m}m {s:02d}s" if m else f"{s}s"
        except Exception:
            pass
    return None


def _stats():
    out = {}

    # Queue
    q = _read(os.path.join(_DATA, 'teacher_queue.json'))
    out['queue'] = len(q) if isinstance(q, list) else 0

    # Teacher stats
    ts = _read(os.path.join(_DATA, 'teacher_stats.json'))
    if ts:
        out['accepted']  = ts.get('total_accepted', 0)
        out['sentences'] = ts.get('total_sentences', 0)
    else:
        out['accepted'] = out['sentences'] = 0

    # Graph — read from DB metadata or count nodes in queue
    # Quick proxy: teacher_stats total_sentences ≈ graph richness
    # Try graph node count via SQLite
    db_path = os.path.join(_DATA, 'graph.db')
    try:
        import sqlite3
        with sqlite3.connect(db_path) as con:
            out['nodes'] = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            out['edges'] = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    except Exception:
        out['nodes'] = out['edges'] = '?'

    # Stage
    stage_data = _read(os.path.join(_DATA, 'discovery_stage.json'))
    out['stage'] = stage_data.get('stage', 0) if stage_data else 0

    return out


# ------------------------------------------------------------------ #
# Overlay window                                                       #
# ------------------------------------------------------------------ #

class Overlay:
    FULL_H    = 225
    COMPACT_H = 54
    WIDTH     = 220

    def __init__(self):
        self.root = tk.Tk()
        self.compact = False
        self._drag_x = self._drag_y = 0

        r = self.root
        r.title('')
        r.overrideredirect(True)          # no title bar
        r.wm_attributes('-topmost', True) # always on top
        r.wm_attributes('-alpha', 0.88)   # slight transparency
        r.configure(bg=BG)
        r.resizable(False, False)

        # Position bottom-right of primary screen
        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        x  = sw - self.WIDTH - 16
        y  = sh - self.FULL_H - 48
        r.geometry(f'{self.WIDTH}x{self.FULL_H}+{x}+{y}')

        self._build_ui()
        self._bind_drag()

        # Start update loop
        self._update()

    # -------------------------------------------------------------- #
    # UI construction                                                  #
    # -------------------------------------------------------------- #

    def _build_ui(self):
        r = self.root

        # Top bar: status dot + label + close button
        bar = tk.Frame(r, bg='#1a1a1a', height=28)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        self._dot = tk.Label(bar, text='●', font=('Consolas', 10),
                             bg='#1a1a1a', fg=DIM)
        self._dot.pack(side='left', padx=(8, 4), pady=4)

        self._status_lbl = tk.Label(bar, text='idle', font=FONT_SM,
                                    bg='#1a1a1a', fg=DIM)
        self._status_lbl.pack(side='left', pady=4)

        self._elapsed_lbl = tk.Label(bar, text='', font=FONT_SM,
                                     bg='#1a1a1a', fg='#444444')
        self._elapsed_lbl.pack(side='left', padx=2)

        close_btn = tk.Label(bar, text='✕', font=FONT_SM,
                             bg='#1a1a1a', fg='#444444', cursor='hand2')
        close_btn.pack(side='right', padx=8)
        close_btn.bind('<Button-1>', lambda e: self.root.destroy())

        toggle_btn = tk.Label(bar, text='⊟', font=FONT_SM,
                              bg='#1a1a1a', fg='#444444', cursor='hand2')
        toggle_btn.pack(side='right', padx=4)
        toggle_btn.bind('<Button-1>', lambda e: self._toggle_compact())

        # Body
        self._body = tk.Frame(r, bg=BG)
        self._body.pack(fill='both', expand=True, padx=10, pady=6)

        # Row builder helper
        def row(label, attr):
            f = tk.Frame(self._body, bg=BG)
            f.pack(fill='x', pady=1)
            tk.Label(f, text=label, font=FONT_SM, bg=BG, fg=DIM,
                     width=10, anchor='w').pack(side='left')
            lbl = tk.Label(f, text='—', font=FONT_MONO, bg=BG, fg=FG, anchor='e')
            lbl.pack(side='right')
            setattr(self, attr, lbl)

        # System
        tk.Label(self._body, text='SYSTEM', font=FONT_SM,
                 bg=BG, fg='#333333').pack(anchor='w', pady=(2, 0))
        row('CPU', '_cpu_lbl')
        row('RAM', '_ram_lbl')

        tk.Frame(self._body, bg='#222222', height=1).pack(fill='x', pady=4)

        # Engine
        tk.Label(self._body, text='ENGINE', font=FONT_SM,
                 bg=BG, fg='#333333').pack(anchor='w', pady=(0, 0))
        row('nodes', '_nodes_lbl')
        row('edges', '_edges_lbl')
        row('queue', '_queue_lbl')
        row('accepted', '_accepted_lbl')
        row('sentences', '_sentences_lbl')
        row('stage', '_stage_lbl')
        row('growth', '_growth_lbl')

        self._warn_lbl = tk.Label(self._body, text='', font=FONT_SM,
                                  bg=BG, fg=RED, wraplength=190, justify='left')
        self._warn_lbl.pack(anchor='w', pady=(2, 0))

    # -------------------------------------------------------------- #
    # Drag to reposition                                               #
    # -------------------------------------------------------------- #

    def _bind_drag(self):
        self.root.bind('<ButtonPress-1>',   self._drag_start)
        self.root.bind('<B1-Motion>',       self._drag_move)

    def _drag_start(self, e):
        self._drag_x = e.x
        self._drag_y = e.y

    def _drag_move(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f'+{x}+{y}')

    # -------------------------------------------------------------- #
    # Compact toggle                                                   #
    # -------------------------------------------------------------- #

    def _toggle_compact(self):
        self.compact = not self.compact
        h = self.COMPACT_H if self.compact else self.FULL_H
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self.root.geometry(f'{self.WIDTH}x{h}+{x}+{y}')
        if self.compact:
            self._body.pack_forget()
        else:
            self._body.pack(fill='both', expand=True, padx=10, pady=6)

    # -------------------------------------------------------------- #
    # Periodic update                                                  #
    # -------------------------------------------------------------- #

    def _update(self):
        try:
            status  = _engine_status()
            colour  = STATUS_COLOURS.get(status, DIM)
            elapsed = _fetch_elapsed()

            self._dot.config(fg=colour)
            self._status_lbl.config(text=status, fg=colour)
            self._elapsed_lbl.config(text=elapsed or '')

            if not self.compact:
                # System stats
                if HAS_PSUTIL:
                    cpu = psutil.cpu_percent(interval=None)
                    ram = psutil.virtual_memory()
                    cpu_col = RED if cpu > 80 else ORANGE if cpu > 50 else GREEN
                    ram_col = RED if ram.percent > 85 else ORANGE if ram.percent > 65 else FG
                    self._cpu_lbl.config(text=f'{cpu:.1f}%', fg=cpu_col)
                    self._ram_lbl.config(
                        text=f'{ram.percent:.1f}%  {ram.used/1e9:.1f}G', fg=ram_col)
                else:
                    self._cpu_lbl.config(text='no psutil', fg=DIM)
                    self._ram_lbl.config(text='pip install psutil', fg=DIM)

                # Engine stats
                s = _stats()
                self._nodes_lbl.config(text=str(s['nodes']))
                self._edges_lbl.config(text=str(s['edges']))

                q_col = GREEN if s['queue'] >= 5 else ORANGE if s['queue'] > 0 else RED
                self._queue_lbl.config(text=str(s['queue']), fg=q_col)
                self._accepted_lbl.config(text=str(s['accepted']))
                self._sentences_lbl.config(text=f"{s['sentences']:,}")

                from auto_discover import CURRICULUM
                stage_label = CURRICULUM[s['stage']]['label'] if s['stage'] < len(CURRICULUM) else str(s['stage'])
                self._stage_lbl.config(text=stage_label[:18])

                # Growth rate from log
                growth_str = '—'
                try:
                    glog = os.path.join(_DATA, 'growth_log.jsonl')
                    lines = open(glog, encoding='utf-8').readlines()
                    if len(lines) >= 2:
                        first = json.loads(lines[0])
                        last  = json.loads(lines[-1])
                        delta_nodes = last['nodes'] - first['nodes']
                        growth_str  = f"+{delta_nodes:,} total"
                except Exception:
                    pass
                self._growth_lbl.config(text=growth_str)

                # Graph size warning
                warn = ''
                if isinstance(s['nodes'], int):
                    if s['nodes'] >= 150_000:
                        warn = f"CRITICAL: {s['nodes']:,} nodes — pause learning!"
                    elif s['nodes'] >= 50_000:
                        warn = f"Large: {s['nodes']:,} nodes — monitor RAM"
                self._warn_lbl.config(text=warn)

        except Exception:
            pass

        self.root.after(2000, self._update)

    def run(self):
        self.root.mainloop()


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == '__main__':
    sys.path.insert(0, os.path.join(_ROOT, 'src'))
    Overlay().run()
