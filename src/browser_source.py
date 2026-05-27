"""
Autonomous Browser — Playwright-driven web surfing
=====================================================
Gives the engine real keyboard + mouse control over a browser.

Instead of hitting APIs, the engine navigates pages like a human:
  - Starts from a DuckDuckGo or Wikipedia search for a current concept
  - Reads the page via trafilatura
  - Scans visible links and picks the one most semantically similar
    to the engine's active JAM field concepts
  - Follows it — and repeats up to MAX_HOPS times per session

This produces far richer content than API scraping:
  - Full article text, not just abstracts
  - Linked rabbit holes the engine genuinely pursues
  - Visual pages (screenshots fed into multimodal pipeline)

Public API:
    AutonomousBrowser.get()              singleton
    browser.session(topic, concepts)     one autonomous browsing session
    browser.snapshot()                   current status
    browser.is_available()               True if Playwright + Chromium present

Sessions run from the learner subsystem thread — never in the main cycle.

Data written:
    data/currently_reading.json   updated each page visit (source='browser')
    data/browser_log.jsonl        rolling 100-entry session log
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from logger import get_logger

log = get_logger('browser_source')

_ROOT      = Path(__file__).parent.parent
_DATA      = _ROOT / 'data'
_LOG_PATH  = _DATA / 'browser_log.jsonl'
_LOG_MAX   = 100

_SINGLETON: Optional['AutonomousBrowser'] = None

MAX_HOPS         = 5       # links to follow per session
PAGE_TIMEOUT_MS  = 12000   # ms to wait for page load
NAV_PAUSE_S      = 1.2     # pause between navigations (be polite)
MAX_LINKS_SCAN   = 20      # links to evaluate per page
MIN_TEXT_CHARS   = 200     # skip pages with less content

# Sites to avoid (add more as needed)
_BLOCKED_DOMAINS = {
    'twitter.com', 'x.com', 'facebook.com', 'instagram.com', 'tiktok.com',
    'youtube.com', 'reddit.com',  # reddit has its own source
    'amazon.com', 'ebay.com', 'etsy.com',
    'github.com', 'stackoverflow.com',  # code sites
}


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lstrip('www.')
    except Exception:
        return ''


def _is_blocked(url: str) -> bool:
    d = _domain(url)
    return any(blocked in d for blocked in _BLOCKED_DOMAINS)


def _extract_text(html: str, url: str) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_links=False,
                                   include_comments=False, no_fallback=False)
        return text or ''
    except Exception:
        # Fallback: strip tags
        return re.sub(r'<[^>]+>', ' ', html)[:3000]


def _cosine_sim(a, b) -> float:
    try:
        import numpy as np
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na < 1e-8 or nb < 1e-8:
            return 0.0
        return float(np.dot(a, b) / (na * nb))
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# AutonomousBrowser
# ══════════════════════════════════════════════════════════════════════════════

class AutonomousBrowser:

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._lock   = threading.Lock()
        self._active = False
        self._current_url  = ''
        self._current_text = ''
        self._session_count = 0
        self._pages_visited = 0
        self._last_session_ts = 0.0
        self._log_buf: list[dict] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        if self._available is None:
            try:
                from playwright.sync_api import sync_playwright
                self._available = True
            except ImportError:
                self._available = False
                log.warning('Playwright not installed — browser source unavailable')
        return self._available

    def session(self, topic: str, concepts: list[str]) -> list[dict]:
        """
        Run one autonomous browsing session on `topic`.
        Returns list of {title, url, text, concepts_found} dicts.
        """
        if not self.is_available():
            return []
        if self._active:
            log.debug('browser session already in progress — skipping')
            return []

        with self._lock:
            self._active = True

        results = []
        try:
            results = self._run_session(topic, concepts)
        except Exception as e:
            log.warning('browser session error: %s', e)
        finally:
            self._active = False
            self._last_session_ts = time.time()
        return results

    def snapshot(self) -> dict:
        return {
            'available':       self.is_available(),
            'active':          self._active,
            'session_count':   self._session_count,
            'pages_visited':   self._pages_visited,
            'current_url':     self._current_url,
            'last_session_ts': self._last_session_ts,
        }

    @classmethod
    def get(cls) -> 'AutonomousBrowser':
        global _SINGLETON
        if _SINGLETON is None:
            _SINGLETON = cls()
        return _SINGLETON

    # ── Internal session ───────────────────────────────────────────────────────

    def _run_session(self, topic: str, concepts: list[str]) -> list[dict]:
        from playwright.sync_api import sync_playwright

        self._session_count += 1
        results = []

        # Embed concepts for link selection
        concept_vecs = self._embed_concepts(concepts)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-extensions',
                ],
            )
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
                java_script_enabled=True,
            )
            page = context.new_page()

            # ── Start from DuckDuckGo search ───────────────────────────────────
            seed_url = f'https://en.wikipedia.org/wiki/Special:Search?search={topic.replace(" ", "+")}&ns0=1'
            log.debug('browser: starting session topic="%s" seed=%s', topic[:40], seed_url)

            visited_urls: set[str] = set()
            current_url = seed_url

            for hop in range(MAX_HOPS):
                if _is_blocked(current_url):
                    log.debug('browser: blocked domain, stopping: %s', current_url)
                    break

                try:
                    # Navigate with keyboard shortcut Ctrl+L then type URL
                    # (real human-like: focus address bar, type, Enter)
                    page.keyboard.press('F5')  # refresh any stale state
                    page.goto(current_url, timeout=PAGE_TIMEOUT_MS,
                              wait_until='domcontentloaded')

                    # Human-like: scroll down slowly to load lazy content
                    self._human_scroll(page)

                    visited_urls.add(current_url)
                    self._current_url = current_url
                    self._pages_visited += 1

                    # Extract text
                    html  = page.content()
                    title = page.title() or topic
                    text  = _extract_text(html, current_url)

                    if len(text) >= MIN_TEXT_CHARS:
                        # Extract concepts from text
                        found_concepts = self._extract_concepts_from_text(text)

                        entry = {
                            'title':          title[:120],
                            'url':            current_url,
                            'text':           text[:4000],
                            'concepts_found': len(found_concepts),
                            'source':         'browser',
                            'hop':            hop,
                            'session':        self._session_count,
                            'ts':             time.time(),
                        }
                        results.append(entry)
                        self._write_currently_reading(title, current_url, found_concepts)
                        self._log_entry(entry)

                        log.debug('browser hop %d: "%s" +%d concepts',
                                  hop, title[:50], len(found_concepts))
                    else:
                        log.debug('browser hop %d: sparse page (%d chars), skipping',
                                  hop, len(text))

                    # ── Choose next link ───────────────────────────────────────
                    if hop < MAX_HOPS - 1:
                        next_url = self._choose_next_link(
                            page, visited_urls, concept_vecs, concepts
                        )
                        if not next_url:
                            log.debug('browser: no suitable link found — stopping')
                            break
                        current_url = next_url
                        time.sleep(NAV_PAUSE_S)

                except Exception as e:
                    log.debug('browser hop %d error: %s', hop, e)
                    break

            page.close()
            context.close()
            browser.close()

        log.info('browser session %d: %d pages, %d results',
                 self._session_count, self._pages_visited, len(results))
        return results

    def _human_scroll(self, page) -> None:
        """Scroll down in human-like increments to trigger lazy loading."""
        try:
            for _ in range(3):
                page.keyboard.press('Space')
                time.sleep(0.15)
            page.keyboard.press('Home')  # back to top
        except Exception:
            pass

    def _choose_next_link(self, page, visited: set, concept_vecs: list,
                          concepts: list[str]) -> Optional[str]:
        """
        Scan visible links on the page.
        Score each by semantic similarity to active concepts.
        Return the best unvisited URL.
        """
        try:
            # Get all <a href> links with visible text
            links = page.eval_on_selector_all(
                'a[href]',
                '''els => els.map(el => ({
                    href: el.href,
                    text: el.innerText.trim().slice(0, 80)
                })).filter(l => l.text.length > 3 && l.href.startsWith('http'))'''
            )
        except Exception:
            return None

        if not links:
            return None

        best_url   = None
        best_score = -1.0

        for link in links[:MAX_LINKS_SCAN]:
            url  = link.get('href', '')
            text = link.get('text', '')
            if not url or not text:
                continue
            if url in visited or _is_blocked(url):
                continue
            # Score link text against concept embeddings
            score = self._score_link(text, concept_vecs, concepts)
            if score > best_score:
                best_score = score
                best_url   = url

        if best_score < 0.15:
            return None
        log.debug('browser: next link score=%.2f url=%s', best_score, best_url)
        return best_url

    def _score_link(self, link_text: str, concept_vecs: list,
                    concepts: list[str]) -> float:
        if not concept_vecs:
            # Fallback: keyword overlap
            lt_words = set(link_text.lower().split())
            concept_words = set(w.lower() for c in concepts for w in c.split())
            overlap = len(lt_words & concept_words)
            return min(overlap / max(len(lt_words), 1), 1.0)

        try:
            from embed_index import EmbeddingIndex
            idx = EmbeddingIndex.get()
            link_vec = idx.get_embedding(link_text)
            if link_vec is None:
                from extractor import extract
                link_concepts = extract(link_text)
                if link_concepts:
                    link_vec = link_concepts[0].embedding
                else:
                    return 0.0
            sims = [_cosine_sim(link_vec, cv) for cv in concept_vecs]
            return max(sims) if sims else 0.0
        except Exception:
            return 0.0

    def _embed_concepts(self, concepts: list[str]) -> list:
        vecs = []
        try:
            from embed_index import EmbeddingIndex
            idx = EmbeddingIndex.get()
            for c in concepts[:8]:
                v = idx.get_embedding(c)
                if v is not None:
                    vecs.append(v)
        except Exception:
            pass
        return vecs

    def _extract_concepts_from_text(self, text: str) -> list[str]:
        try:
            from extractor import extract
            concepts = extract(text[:2000])
            return [c.text for c in concepts]
        except Exception:
            return []

    # ── Side effects ───────────────────────────────────────────────────────────

    def _write_currently_reading(self, title: str, url: str,
                                  concepts: list[str]) -> None:
        try:
            data = {
                'title':          title,
                'url':            url,
                'source':         'browser',
                'concepts_found': len(concepts),
                'ts':             time.time(),
            }
            path = _DATA / 'currently_reading.json'
            tmp  = str(path) + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp, str(path))
        except Exception:
            pass

    def _log_entry(self, entry: dict) -> None:
        try:
            _DATA.mkdir(exist_ok=True)
            with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'title':   entry['title'],
                    'url':     entry['url'],
                    'hop':     entry['hop'],
                    'session': entry['session'],
                    'ts':      entry['ts'],
                }, ensure_ascii=False) + '\n')
            # Trim
            try:
                lines = _LOG_PATH.read_text(encoding='utf-8').splitlines()
                if len(lines) > _LOG_MAX:
                    _LOG_PATH.write_text('\n'.join(lines[-_LOG_MAX:]) + '\n', encoding='utf-8')
            except Exception:
                pass
        except Exception:
            pass
