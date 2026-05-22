"""
Node [B] — Sources
=================
Multi-source discovery — search open libraries and the web.
No state, no threads, no side-effects.  Pure search + fetch functions.

Sources (all free, no API key):
  wikipedia  — encyclopaedic articles via Wikipedia REST API
  simple_wiki— Simple English Wikipedia (children / ESL audience)
  arxiv      — academic paper abstracts via arXiv API
  gutenberg  — public-domain books via Gutendex API
  reddit     — public posts via Reddit JSON API
  openalex   — academic paper abstracts via OpenAlex API
  web        — general web via DuckDuckGo + trafilatura

Public API
----------
SOURCES             dict   {name: (search_fn, fetch_fn)}
SOURCE_LABELS       dict   {name: emoji-prefixed display label}
SOURCE_DESCRIPTIONS dict   {name: one-line description}
search_sources()    search several sources in one call
fetch_content()     fetch full sentences from a search-result item
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

import requests
import spacy
import trafilatura

HEADERS = {'User-Agent': 'AmbiguityEngine/0.1 (research toy; contact: local)'}


def _get(url: str, params: dict | None = None, retries: int = 2, timeout: int = 12) -> requests.Response:
    """GET with simple exponential backoff on 429 / 5xx."""
    for attempt in range(retries):
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


# ======================================================================== #
# Text utilities — sentence splitter + readability score                    #
# ======================================================================== #

_nlp = None

def _sentences(text: str, min_len: int = 40, max_len: int = 400) -> list[str]:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load('en_core_web_sm')
    doc = _nlp(text[:60_000])
    out = []
    for sent in doc.sents:
        s = re.sub(r'\s+', ' ', sent.text.strip())
        if min_len <= len(s) <= max_len and s.count(' ') >= 4:
            out.append(s)
    return out


def _flesch_score(text: str) -> float:
    """Flesch Reading Ease approximation. 100=very easy, 0=very hard."""
    if not text:
        return 50.0
    sentences = [s for s in re.split(r'[.!?]+', text) if s.strip()]
    words = text.split()
    if not sentences or not words:
        return 50.0
    syllables = sum(max(1, len(re.findall(r'[aeiouAEIOU]+', w))) for w in words)
    asl = len(words) / len(sentences)
    asw = syllables / len(words)
    score = 206.835 - 1.015 * asl - 84.6 * asw
    return max(0.0, min(100.0, score))


# ======================================================================== #
# Wikipedia                                                                 #
# ======================================================================== #

def search_wikipedia(query: str, max_results: int = 10) -> list[dict]:
    url = 'https://en.wikipedia.org/w/api.php'
    params = {
        'action': 'query', 'list': 'search',
        'srsearch': query, 'srlimit': max_results,
        'utf8': 1, 'format': 'json',
    }
    r = _get(url, params=params)
    results = []
    for item in r.json().get('query', {}).get('search', []):
        snippet = re.sub(r'<[^>]+>', '', item.get('snippet', ''))
        results.append({
            'title':   item['title'],
            'url':     f"https://en.wikipedia.org/wiki/{item['title'].replace(' ', '_')}",
            'snippet': snippet,
            'source':  'wikipedia',
        })
    return results


def fetch_wikipedia(url: str) -> list[str]:
    title = url.split('/wiki/')[-1].replace('_', ' ')
    api = 'https://en.wikipedia.org/w/api.php'
    params = {
        'action': 'query', 'prop': 'extracts',
        'exintro': False, 'explaintext': True,
        'titles': title, 'format': 'json',
    }
    r = _get(api, params=params)
    pages = r.json().get('query', {}).get('pages', {})
    text  = next(iter(pages.values()), {}).get('extract', '')
    return _sentences(text)


# ======================================================================== #
# Simple English Wikipedia (children / ESL audience)                        #
# ======================================================================== #

def search_simple_wikipedia(query: str, max_results: int = 10) -> list[dict]:
    url = 'https://simple.wikipedia.org/w/api.php'
    params = {
        'action': 'query', 'list': 'search',
        'srsearch': query, 'srlimit': max_results,
        'utf8': 1, 'format': 'json',
    }
    r = _get(url, params=params)
    results = []
    for item in r.json().get('query', {}).get('search', []):
        snippet = re.sub(r'<[^>]+>', '', item.get('snippet', ''))
        results.append({
            'title':   item['title'],
            'url':     f"https://simple.wikipedia.org/wiki/{item['title'].replace(' ', '_')}",
            'snippet': snippet,
            'source':  'simple_wiki',
        })
    return results


def fetch_simple_wikipedia(url: str) -> list[str]:
    title = url.split('/wiki/')[-1].replace('_', ' ')
    api = 'https://simple.wikipedia.org/w/api.php'
    params = {
        'action': 'query', 'prop': 'extracts',
        'exintro': False, 'explaintext': True,
        'titles': title, 'format': 'json',
    }
    r = _get(api, params=params)
    pages = r.json().get('query', {}).get('pages', {})
    text  = next(iter(pages.values()), {}).get('extract', '')
    return _sentences(text, min_len=20)


# ======================================================================== #
# arXiv                                                                     #
# ======================================================================== #

def search_arxiv(query: str, max_results: int = 10,
                 year_from: int | None = None, year_to: int | None = None) -> list[dict]:
    url = 'https://export.arxiv.org/api/query'
    q = f'all:{query}'
    if year_from or year_to:
        y0 = f'{year_from or 1990}0101'
        y1 = f'{year_to or 2099}1231'
        q += f' AND submittedDate:[{y0} TO {y1}]'
    params = {
        'search_query': q,
        'start': 0, 'max_results': max_results,
        'sortBy': 'relevance',
    }
    r = _get(url, params=params, timeout=15)
    text = r.text.strip().lstrip('﻿')
    ns   = {'atom': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(text)
    results = []
    for entry in root.findall('atom:entry', ns):
        title   = (entry.findtext('atom:title', '', ns) or '').strip().replace('\n', ' ')
        summary = (entry.findtext('atom:summary', '', ns) or '').strip().replace('\n', ' ')
        link    = next(
            (l.get('href', '') for l in entry.findall('atom:link', ns)
             if l.get('type') == 'text/html'),
            ''
        )
        if not link:
            id_text = (entry.findtext('atom:id', '', ns) or '').strip()
            link = id_text.replace('http://', 'https://')
        results.append({
            'title':   title,
            'url':     link,
            'snippet': summary[:300],
            'source':  'arxiv',
        })
    return results


def fetch_arxiv(url: str) -> list[str]:
    r = _get(url)
    raw = trafilatura.extract(r.text, include_comments=False, include_tables=False)
    return _sentences(raw or '')


# ======================================================================== #
# Project Gutenberg (via Gutendex)                                          #
# ======================================================================== #

def search_gutenberg(query: str, max_results: int = 10, topic: str = '') -> list[dict]:
    params: dict = {'search': query, 'languages': 'en'}
    if topic:
        params['topic'] = topic
    r = _get('https://gutendex.com/books/', params=params)
    books = sorted(
        r.json().get('results', []),
        key=lambda b: b.get('download_count', 0),
        reverse=True,
    )
    results = []
    for book in books[:max_results]:
        book_id = book['id']
        title   = book.get('title', 'Unknown')
        authors = ', '.join(a['name'] for a in book.get('authors', []))
        results.append({
            'title':   f"{title} — {authors}",
            'url':     f"https://www.gutenberg.org/ebooks/{book_id}",
            'snippet': f"Public-domain book · {book.get('download_count', 0):,} downloads",
            'source':  'gutenberg',
            '_book_id': book_id,
            '_formats': book.get('formats', {}),
        })
    return results


def fetch_gutenberg(url: str, item: dict | None = None) -> list[str]:
    """Download the plain-text version of a Gutenberg book (first 80 KB)."""
    formats = (item or {}).get('_formats', {})
    txt_url = (
        formats.get('text/plain; charset=us-ascii')
        or formats.get('text/plain')
        or formats.get('text/plain; charset=utf-8')
    )
    if not txt_url:
        book_id = url.rstrip('/').split('/')[-1]
        txt_url = f"https://www.gutenberg.org/files/{book_id}/{book_id}-0.txt"
    try:
        r = _get(txt_url)
    except Exception:
        return []
    text = r.text[2000:82000]  # skip Gutenberg header
    return _sentences(text)


# ======================================================================== #
# Reddit                                                                    #
# ======================================================================== #

def search_reddit(query: str, max_results: int = 10) -> list[dict]:
    url = 'https://www.reddit.com/search.json'
    params = {'q': query, 'sort': 'relevance', 'limit': max_results, 't': 'year'}
    r = _get(url, params=params)
    results = []
    for child in r.json().get('data', {}).get('children', []):
        d = child['data']
        if d.get('is_self') and d.get('selftext'):
            snippet = d['selftext'][:300].replace('\n', ' ')
        else:
            snippet = d.get('title', '')
        results.append({
            'title':   d.get('title', ''),
            'url':     f"https://www.reddit.com{d.get('permalink', '')}",
            'snippet': snippet,
            'source':  'reddit',
            '_selftext': d.get('selftext', ''),
        })
    return results


def fetch_reddit(url: str, item: dict | None = None) -> list[str]:
    text = (item or {}).get('_selftext', '')
    if not text:
        r = _get(url + '.json')
        try:
            posts = r.json()
            text  = posts[0]['data']['children'][0]['data'].get('selftext', '')
        except Exception:
            text = ''
    if not text:
        return []
    return _sentences(text)


# ======================================================================== #
# OpenAlex (academic papers, free, no key)                                  #
# ======================================================================== #

def search_openalex(query: str, max_results: int = 10) -> list[dict]:
    url = 'https://api.openalex.org/works'
    params = {
        'search': query, 'per-page': max_results,
        'select': 'title,abstract_inverted_index,primary_location',
    }
    r = _get(url, params=params)
    results = []
    for work in r.json().get('results', []):
        title    = work.get('title') or ''
        abstract = _reconstruct_abstract(work.get('abstract_inverted_index') or {})
        loc      = work.get('primary_location') or {}
        landing  = (loc.get('landing_page_url') or loc.get('pdf_url') or '')
        if not landing:
            continue
        results.append({
            'title':     title,
            'url':       landing,
            'snippet':   abstract[:300],
            'source':    'openalex',
            '_abstract': abstract,
        })
    return results


def _reconstruct_abstract(inv: dict) -> str:
    """OpenAlex stores abstracts as inverted index — reconstruct word order."""
    if not inv:
        return ''
    words = {}
    for word, positions in inv.items():
        for pos in positions:
            words[pos] = word
    return ' '.join(words[p] for p in sorted(words))


def fetch_openalex(url: str, item: dict | None = None) -> list[str]:
    abstract = (item or {}).get('_abstract', '')
    if abstract:
        return _sentences(abstract)
    raw = trafilatura.fetch_url(url)
    text = trafilatura.extract(raw or '', include_comments=False) or ''
    return _sentences(text)


# ======================================================================== #
# General web (DuckDuckGo + trafilatura)                                    #
# ======================================================================== #

def search_web(query: str, max_results: int = 10) -> list[dict]:
    from ddgs import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                'title':   r.get('title', ''),
                'url':     r.get('href', ''),
                'snippet': r.get('body', ''),
                'source':  'web',
            })
    return results


def fetch_web(url: str, item: dict | None = None) -> list[str]:
    raw  = trafilatura.fetch_url(url)
    text = trafilatura.extract(raw or '', include_comments=False, include_tables=False) or ''
    return _sentences(text)


# ======================================================================== #
# Unified API                                                               #
# ======================================================================== #

SOURCES = {
    'wikipedia':    (search_wikipedia,        fetch_wikipedia),
    'simple_wiki':  (search_simple_wikipedia, fetch_simple_wikipedia),
    'arxiv':        (search_arxiv,            fetch_arxiv),
    'gutenberg':    (search_gutenberg,        fetch_gutenberg),
    'reddit':       (search_reddit,           fetch_reddit),
    'openalex':     (search_openalex,         fetch_openalex),
    'web':          (search_web,              fetch_web),
}

SOURCE_LABELS = {
    'wikipedia':   '📖 Wikipedia',
    'simple_wiki': '🟦 Simple Wiki',
    'arxiv':       '🔬 arXiv',
    'gutenberg':   '📚 Gutenberg',
    'reddit':      '💬 Reddit',
    'openalex':    '🎓 OpenAlex',
    'web':         '🌐 Web',
}

SOURCE_DESCRIPTIONS = {
    'wikipedia':   'Encyclopaedic articles — factual, structured prose',
    'simple_wiki': 'Simple English Wikipedia — written for children and ESL readers',
    'arxiv':       'Academic preprints — CS, physics, biology, economics',
    'gutenberg':   'Public-domain books — literature, poetry, philosophy',
    'reddit':      'Forum posts — conversational, opinion-heavy',
    'openalex':    'Academic abstracts from 200M+ open-access papers',
    'web':         'General web pages via DuckDuckGo',
}


def search_sources(query: str, sources: list[str], max_per_source: int = 6) -> list[dict]:
    results = []
    for src in sources:
        search_fn, _ = SOURCES[src]
        try:
            results.extend(search_fn(query, max_results=max_per_source))
        except Exception as e:
            results.append({
                'title': f'[{src} error]',
                'url': '',
                'snippet': str(e),
                'source': src,
                '_error': True,
            })
    return results


def fetch_content(item: dict) -> list[str]:
    src = item.get('source', 'web')
    _, fetch_fn = SOURCES.get(src, SOURCES['web'])
    return fetch_fn(item['url'], item)
