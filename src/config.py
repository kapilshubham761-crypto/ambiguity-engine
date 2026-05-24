"""
Config — live-reloadable JSON config singleton.

Reads data/engine_config.json every 30 s (only when file mtime changes).
Provides a simple get(*keys, default=) API for dot-path access.
The Settings UI writes to the same file; changes take effect within one cycle.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_ROOT      = Path(__file__).parent.parent
_JSON_PATH = _ROOT / 'data' / 'engine_config.json'
_RELOAD_S  = 30

_SINGLETON: 'Config | None' = None


class Config:
    def __init__(self) -> None:
        self._data:       dict  = {}
        self._mtime:      float = 0.0
        self._last_check: float = 0.0
        self._load()

    # ── internal ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            mtime = _JSON_PATH.stat().st_mtime
            if mtime != self._mtime:
                self._data  = json.loads(_JSON_PATH.read_text(encoding='utf-8'))
                self._mtime = mtime
        except Exception:
            pass
        self._last_check = time.time()

    def _maybe_reload(self) -> None:
        if time.time() - self._last_check >= _RELOAD_S:
            self._load()

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, *keys: str, default: Any = None) -> Any:
        """Traverse nested keys; return default if any key is missing."""
        self._maybe_reload()
        node = self._data
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k)
            if node is None:
                return default
        return node

    def section(self, key: str) -> dict:
        self._maybe_reload()
        return dict(self._data.get(key, {}))

    def all(self) -> dict:
        self._maybe_reload()
        return dict(self._data)

    def save(self, data: dict) -> None:
        """Atomically overwrite engine_config.json with *data*."""
        _JSON_PATH.parent.mkdir(exist_ok=True)
        tmp = _JSON_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        tmp.replace(_JSON_PATH)
        self._data       = data
        self._mtime      = _JSON_PATH.stat().st_mtime
        self._last_check = time.time()

    # ── singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> 'Config':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON
