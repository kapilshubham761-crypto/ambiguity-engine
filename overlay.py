"""
Ambiguity Engine — always-on-top stats overlay.
Run: python overlay.py
Drag to reposition. Click ⊟ to toggle compact/full.
"""

import json
import os
import sys
import tkinter as tk
from datetime import datetime, timezone

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    HAS_NVML = True
except Exception:
    HAS_NVML = False
    _GPU_HANDLE = None

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_ROOT, 'data')

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = '#0d0d0d'
FG        = '#cccccc'
DIM       = '#444444'
ACCENT    = '#4a9eff'
GREEN     = '#44ff88'
RED       = '#ff4444'
ORANGE    = '#ffaa44'
PURPLE    = '#bb88ff'
FONT_MONO = ('Consolas', 9)
FONT_SM   = ('Consolas', 8)
FONT_XS   = ('Consolas', 7)

STATUS_COLOURS = {
    'fetching': ACCENT,
    'paused':   RED,
    'idle':     DIM,
}

MODE_COLOURS = {
    'focused':      '#f59e0b',
    'exploratory':  '#3b82f6',
    'associative':  '#8b5cf6',
    'exploitative': '#ef4444',
    'reflective':   '#10b981',
}


# ── Data readers ──────────────────────────────────────────────────────────────

def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _engine_status():
    try:
        with open(os.path.join(_DATA, 'paused.txt')) as f:
            if f.read().strip() == '1':
                return 'paused'
    except Exception:
        pass
    fs = _read(os.path.join(_DATA, 'fetch_status.json'))
    if fs and fs.get('fetching'):
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


def _cog_status():
    cs = _read(os.path.join(_DATA, 'cog_status.json'))
    if cs:
        return cs.get('mode', '—'), cs.get('goal', '—')
    return '—', '—'


def _stats():
    out = {}
    ls = _read(os.path.join(_DATA, 'learner_stats.json'))
    out['sentences'] = ls.get('total_sentences', 0) if ls else 0
    try:
        import sqlite3
        with sqlite3.connect(os.path.join(_DATA, 'graph.db')) as con:
            out['nodes'] = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            out['edges'] = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    except Exception:
        out['nodes'] = out['edges'] = '?'
    return out


# ── Overlay ───────────────────────────────────────────────────────────────────

class Overlay:
    FULL_H    = 290
    COMPACT_H = 28
    WIDTH     = 220

    def __init__(self):
        self.root    = tk.Tk()
        self.compact = False
        self._drag_x = self._drag_y = 0

        r = self.root
        r.title('')
        r.overrideredirect(True)
        r.wm_attributes('-topmost', True)
        r.wm_attributes('-alpha', 0.90)
        r.configure(bg=BG)
        r.resizable(False, False)

        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        r.geometry(f'{self.WIDTH}x{self.FULL_H}+{sw - self.WIDTH - 16}+{sh - self.FULL_H - 48}')

        self._build_ui()
        self._bind_drag()
        self._update()

    def _build_ui(self):
        r = self.root

        # ── Top bar ───────────────────────────────────────────────────
        bar = tk.Frame(r, bg='#111111', height=28)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        self._dot = tk.Label(bar, text='●', font=('Consolas', 9),
                             bg='#111111', fg=DIM)
        self._dot.pack(side='left', padx=(8, 3), pady=4)

        self._status_lbl = tk.Label(bar, text='idle', font=FONT_SM,
                                    bg='#111111', fg=DIM)
        self._status_lbl.pack(side='left', pady=4)

        self._elapsed_lbl = tk.Label(bar, text='', font=FONT_XS,
                                     bg='#111111', fg='#333333')
        self._elapsed_lbl.pack(side='left', padx=2)

        tk.Label(bar, text='✕', font=FONT_SM, bg='#111111', fg='#333333',
                 cursor='hand2').pack(side='right', padx=8).bind(
                     '<Button-1>', lambda e: self.root.destroy()) if False else None
        _close = tk.Label(bar, text='✕', font=FONT_SM, bg='#111111', fg='#333333', cursor='hand2')
        _close.pack(side='right', padx=8)
        _close.bind('<Button-1>', lambda e: self.root.destroy())

        _tog = tk.Label(bar, text='⊟', font=FONT_SM, bg='#111111', fg='#333333', cursor='hand2')
        _tog.pack(side='right', padx=2)
        _tog.bind('<Button-1>', lambda e: self._toggle_compact())

        # ── Body ──────────────────────────────────────────────────────
        self._body = tk.Frame(r, bg=BG)
        self._body.pack(fill='both', expand=True, padx=10, pady=4)

        def row(label, attr, fg=FG):
            f = tk.Frame(self._body, bg=BG)
            f.pack(fill='x', pady=1)
            tk.Label(f, text=label, font=FONT_XS, bg=BG, fg='#555555',
                     width=10, anchor='w').pack(side='left')
            lbl = tk.Label(f, text='—', font=FONT_MONO, bg=BG, fg=fg, anchor='e')
            lbl.pack(side='right')
            setattr(self, attr, lbl)

        def sep():
            tk.Frame(self._body, bg='#1e1e1e', height=1).pack(fill='x', pady=3)

        # Cognitive mode + goal
        tk.Label(self._body, text='COGNITION', font=FONT_XS,
                 bg=BG, fg='#2a2a2a').pack(anchor='w', pady=(2, 0))
        row('mode',  '_mode_lbl', PURPLE)
        row('goal',  '_goal_lbl', ACCENT)

        sep()

        # System
        tk.Label(self._body, text='SYSTEM', font=FONT_XS,
                 bg=BG, fg='#2a2a2a').pack(anchor='w')
        row('CPU',  '_cpu_lbl')
        row('RAM',  '_ram_lbl')
        row('GPU',  '_gpu_lbl')

        sep()

        # Engine
        tk.Label(self._body, text='ENGINE', font=FONT_XS,
                 bg=BG, fg='#2a2a2a').pack(anchor='w')
        row('nodes',     '_nodes_lbl')
        row('edges',     '_edges_lbl')
        row('sentences', '_sentences_lbl')
        row('growth',    '_growth_lbl')

        sep()

        self._updated_lbl = tk.Label(self._body, text='', font=FONT_XS,
                                     bg=BG, fg='#2a2a2a', anchor='e')
        self._updated_lbl.pack(fill='x')

        self._warn_lbl = tk.Label(self._body, text='', font=FONT_XS,
                                  bg=BG, fg=RED, wraplength=190, justify='left')
        self._warn_lbl.pack(anchor='w')

    def _bind_drag(self):
        self.root.bind('<ButtonPress-1>', lambda e: setattr(self, '_drag_x', e.x) or setattr(self, '_drag_y', e.y))
        self.root.bind('<B1-Motion>',     self._drag_move)

    def _drag_move(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f'+{x}+{y}')

    def _toggle_compact(self):
        self.compact = not self.compact
        h = self.COMPACT_H if self.compact else self.FULL_H
        self.root.geometry(f'{self.WIDTH}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}')
        if self.compact:
            self._body.pack_forget()
        else:
            self._body.pack(fill='both', expand=True, padx=10, pady=4)

    def _update(self):
        try:
            status  = _engine_status()
            colour  = STATUS_COLOURS.get(status, DIM)
            elapsed = _fetch_elapsed()

            self._dot.config(fg=colour)
            self._status_lbl.config(text=status, fg=colour)
            self._elapsed_lbl.config(text=elapsed or '')

            if not self.compact:
                # Cognition
                mode, goal = _cog_status()
                mc = MODE_COLOURS.get(mode, PURPLE)
                self._mode_lbl.config(text=mode, fg=mc)
                self._goal_lbl.config(text=goal, fg=ACCENT)

                # System
                if HAS_PSUTIL:
                    cpu = psutil.cpu_percent(interval=None)
                    ram = psutil.virtual_memory()
                    self._cpu_lbl.config(
                        text=f'{cpu:.1f}%',
                        fg=RED if cpu > 80 else ORANGE if cpu > 50 else GREEN)
                    self._ram_lbl.config(
                        text=f'{ram.percent:.1f}%  {ram.used/1e9:.1f}G',
                        fg=RED if ram.percent > 85 else ORANGE if ram.percent > 65 else FG)
                else:
                    self._cpu_lbl.config(text='no psutil', fg=DIM)
                    self._ram_lbl.config(text='—', fg=DIM)

                if HAS_NVML and _GPU_HANDLE:
                    try:
                        util    = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
                        temp    = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                        mem     = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                        gpu_pct = util.gpu
                        self._gpu_lbl.config(
                            text=f'{gpu_pct}%  {mem.used/1e9:.1f}G  {temp}°C',
                            fg=RED if gpu_pct > 80 else ORANGE if gpu_pct > 50 else GREEN)
                    except Exception:
                        self._gpu_lbl.config(text='nvml err', fg=DIM)
                else:
                    self._gpu_lbl.config(text='no nvml', fg=DIM)

                # Engine
                s = _stats()
                self._nodes_lbl.config(text=f"{s['nodes']:,}" if isinstance(s['nodes'], int) else '?')
                self._edges_lbl.config(text=f"{s['edges']:,}" if isinstance(s['edges'], int) else '?')
                self._sentences_lbl.config(text=f"{s['sentences']:,}")

                growth_str = '—'
                try:
                    lines = open(os.path.join(_DATA, 'growth_log.jsonl'), encoding='utf-8').readlines()
                    if len(lines) >= 2:
                        delta = json.loads(lines[-1])['nodes'] - json.loads(lines[0])['nodes']
                        growth_str = f"+{delta:,} nodes"
                except Exception:
                    pass
                self._growth_lbl.config(text=growth_str)

                self._updated_lbl.config(text=f"updated {datetime.now().strftime('%H:%M:%S')}")

                warn = ''
                if isinstance(s['nodes'], int):
                    if s['nodes'] >= 150_000:
                        warn = f"CRITICAL: {s['nodes']:,} nodes!"
                    elif s['nodes'] >= 50_000:
                        warn = f"Large: {s['nodes']:,} nodes"
                self._warn_lbl.config(text=warn)

        except Exception:
            pass

        self.root.after(2000, self._update)

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(_ROOT, 'src'))
    Overlay().run()
