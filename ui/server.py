"""Ambiguity Engine — pure-Python HTTP server. Replaces all Streamlit pages."""
import json, os, re, subprocess, sys, threading, time, uuid, webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'src'))
DATA = ROOT / 'data'
PORT = 8501

try:
    import psutil; _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import pynvml; pynvml.nvmlInit(); _GPU = pynvml.nvmlDeviceGetHandleByIndex(0); _HAS_NVML = True
except Exception:
    _HAS_NVML = False; _GPU = None

# ── Data helpers ──────────────────────────────────────────────────────────────

def _jload(name, default=None):
    try:
        return json.loads((DATA / name).read_text(encoding='utf-8'))
    except Exception:
        return default if default is not None else {}

def _txt(name, default=''):
    try:
        return (DATA / name).read_text(encoding='utf-8').strip()
    except Exception:
        return default

# ── API builders ──────────────────────────────────────────────────────────────

def api_state():
    cog        = _jload('cog_status.json')
    energy     = _jload('energy.json')
    ident      = _jload('identity.json')
    ms_data    = _jload('meta_state.json')
    stats      = _jload('learner_stats.json')
    fetch      = _jload('fetch_status.json')
    paused     = _txt('paused.txt') == '1'
    contra_raw = _jload('contradictions.json', [])
    perf       = _jload('perf_profile.json')
    cr         = _jload('currently_reading.json')

    mode   = cog.get('mode', 'idle')
    goal   = cog.get('goal', 'unknown').replace('_', ' ')
    traits = ident.get('traits', {})
    energy_val = float(energy.get('current', 1.0))

    ms_entries   = ms_data.get('entries', {})
    top_concepts = []
    for k, v in ms_entries.items():
        val = v.get('value', 0) if isinstance(v, dict) else v
        try: top_concepts.append((str(k), float(val)))
        except Exception: pass
    top_concepts.sort(key=lambda x: -x[1])

    # Node/edge counts from EmbeddingIndex (in-memory) or learner_stats fallback
    n_nodes = stats.get('total_concepts', 0)
    n_edges = 0  # no explicit edges in EmbeddingIndex (k-NN is implicit)

    growth_str = '—'
    try:
        gl = (DATA / 'growth_log.jsonl').read_text(encoding='utf-8').splitlines()
        if len(gl) >= 2:
            delta = json.loads(gl[-1])['nodes'] - json.loads(gl[0])['nodes']
            growth_str = f'+{delta:,}'
    except Exception:
        pass

    cpu_s = ram_s = gpu_s = temp_s = '—'
    if _HAS_PSUTIL:
        cpu_s = f'{psutil.cpu_percent(interval=None):.0f}%'
        ram   = psutil.virtual_memory()
        ram_s = f'{ram.used/1e9:.1f}G'
    if _HAS_NVML and _GPU:
        try:
            u = pynvml.nvmlDeviceGetUtilizationRates(_GPU)
            t = pynvml.nvmlDeviceGetTemperature(_GPU, pynvml.NVML_TEMPERATURE_GPU)
            gpu_s = f'{u.gpu}%'; temp_s = f'{t}°C'
        except Exception:
            pass

    elapsed_s = ''
    if fetch.get('fetching') and fetch.get('started_at'):
        try:
            st_ = datetime.fromisoformat(fetch['started_at'])
            if st_.tzinfo is None: st_ = st_.replace(tzinfo=timezone.utc)
            sec = int((datetime.now(tz=timezone.utc) - st_).total_seconds())
            m, s = divmod(sec, 60)
            elapsed_s = f'{m}m{s:02d}s' if m else f'{s}s'
        except Exception:
            pass

    unresolved_c = len([c for c in (contra_raw if isinstance(contra_raw, list) else [])
                        if c.get('resolution_status') == 'open'])

    feed = []
    fp = DATA / 'live_feed.jsonl'
    if fp.exists():
        try:
            # Tail-read: seek to last 32KB instead of loading the entire file
            TAIL = 32768
            with open(fp, 'rb') as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - TAIL))
                raw = fh.read().decode('utf-8', errors='ignore')
            for ln in reversed(raw.splitlines()[-100:]):
                try: feed.append(json.loads(ln))
                except Exception: pass
        except Exception:
            pass

    aiq_cpm = 0.0; aiq_str = aiq_aph_str = '—'
    try:
        ok_feed = [e for e in feed if e.get('status') == 'ok'
                   and e.get('ts') and e.get('concepts', 0) > 0][:30]
        if len(ok_feed) >= 2:
            def _pts(s):
                dt = datetime.fromisoformat(s)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            span_s  = max(1.0, (_pts(ok_feed[0]['ts']) - _pts(ok_feed[-1]['ts'])).total_seconds())
            total_c = sum(e.get('concepts', 0) for e in ok_feed)
            aiq_cpm = total_c / (span_s / 60)
            aiq_str     = f'{aiq_cpm:.1f}/min'
            aiq_aph_str = f'{len(ok_feed)/(span_s/3600):.0f}/hr'
    except Exception:
        pass

    exploration = float(traits.get('exploration_style', 0.3))
    stability   = float(traits.get('stability_bias',    0.5))
    novelty_v   = float(traits.get('novelty_bias',      0.5))

    return {
        'mode': mode, 'goal': goal, 'paused': paused,
        'fetching': bool(fetch.get('fetching')), 'elapsed': elapsed_s,
        'energy': energy_val,
        'spend_count': energy.get('spend_count', 0),
        'denied_count': energy.get('denied_count', 0),
        'traits': traits, 'exploration': exploration, 'stability': stability, 'novelty': novelty_v,
        'top_concepts': top_concepts[:20],
        'coherence': round(sum(v for _, v in top_concepts[:5]) / max(len(top_concepts[:5]), 1), 6) if top_concepts else 0.0,
        'ambiguity': round(exploration * 0.6 + (1 - stability) * 0.4, 2),
        'volatility': round(exploration, 2),
        'n_nodes': n_nodes, 'n_edges': n_edges,
        'n_sentences': stats.get('total_sentences', 0),
        'growth': growth_str, 'aiq': aiq_str, 'aiq_aph': aiq_aph_str, 'aiq_cpm': aiq_cpm,
        'cpu': cpu_s, 'ram': ram_s, 'gpu': gpu_s, 'temp': temp_s,
        'unresolved_contradictions': unresolved_c,
        'last_topic':  feed[0].get('topic',  '—') if feed else '—',
        'last_source': feed[0].get('source', '—') if feed else '—',
        'feed': feed[:50], 'perf': perf, 'currently_reading': cr,
        'now': datetime.now().strftime('%H:%M:%S'),
        'date': datetime.now().strftime('%Y-%m-%d'),
        'browser': _browser_status(),
    }


def _browser_status() -> dict:
    try:
        from browser_source import AutonomousBrowser
        return AutonomousBrowser.get().snapshot()
    except Exception:
        return {'available': False, 'active': False}


def api_field():
    return {
        'field': _jload('jam_field.json'),
        'reg':   _jload('regulation.json'),
        'cog':   _jload('cog_status.json'),
    }


def api_cognition():
    contra_raw = _jload('contradictions.json', [])
    if not isinstance(contra_raw, list): contra_raw = []

    trans_raw = _jload('transitions.json', {})
    paths = []
    _SEP = '__SEP__'
    for key, edge in trans_raw.items():
        if isinstance(edge, dict) and _SEP in key:
            a, b = key.split(_SEP, 1)
            w = float(edge.get('weight', 0.0))
            paths.append((a, b, w))
    paths.sort(key=lambda x: -x[2])

    episodes = []
    ep_path = DATA / 'episodes.jsonl'
    if ep_path.exists():
        try:
            with open(ep_path, 'rb') as fh:
                fh.seek(0, 2); fh.seek(max(0, fh.tell() - 65536))
                raw = fh.read().decode('utf-8', errors='ignore')
            for ln in reversed(raw.splitlines()[-200:]):
                try: episodes.append(json.loads(ln))
                except Exception: pass
        except Exception:
            pass

    abs_data = _jload('abstractions.json', [])
    if isinstance(abs_data, dict): abs_data = abs_data.get('abstractions', [])

    umber = _jload('umber_state.json', {})

    return {
        'contra': contra_raw, 'paths': paths[:30], 'episodes': episodes[:20],
        'worldview': _jload('worldview.json'),
        'abstractions': abs_data,
        'field': _jload('jam_field.json'),
        'reg':   _jload('regulation.json'),
        'cog':   _jload('cog_status.json'),
        'umber': umber,
    }


def api_umber():
    return _jload('umber_state.json', {
        'mode': 'idle', 'goal': 'explore',
        'concepts': 0, 'beliefs': [], 'contradictions': [],
        'goals': {}, 'identity': {}, 'tick': 0,
    })


def api_intent():
    """Last compiled intent + goal graph snapshot."""
    try:
        from intent_compiler import IntentCompiler
        from goal_graph import GoalGraph
        return {
            'last_intent': IntentCompiler.get().last_intent(),
            'goals':       GoalGraph.get().snapshot(),
        }
    except Exception as e:
        return {'error': str(e), 'last_intent': None, 'goals': {}}


def api_feedback():
    """Runtime feedback history and running stats."""
    try:
        from runtime_feedback import RuntimeFeedback
        return RuntimeFeedback.get().snapshot()
    except Exception as e:
        return {'error': str(e)}


def api_echo() -> dict:
    """Lightweight 1-second heartbeat for the voice chat echo pulse."""
    cog    = _jload('cog_status.json')
    cr     = _jload('currently_reading.json')
    stats  = _jload('learner_stats.json')
    fetch  = _jload('fetch_status.json')
    ms_raw = _jload('meta_state.json')

    mode = cog.get('mode', 'idle')
    goal = cog.get('goal', 'explore').replace('_', ' ')

    title  = cr.get('title', '') if cr else ''
    source = cr.get('source', '') if cr else ''
    reading = f'{title[:40]}' if title else '—'
    if source:
        reading += f'  [{source}]'

    total_c = stats.get('total_concepts', 0)

    # Top concept from meta_state
    top_concept = ''
    entries = ms_raw.get('entries', {}) if ms_raw else {}
    if entries:
        best = max(entries.items(), key=lambda x: x[1].get('value', 0) if isinstance(x[1], dict) else x[1], default=(None, 0))
        if best[0]:
            top_concept = str(best[0])[:28]

    fetching = bool(fetch.get('fetching', False)) if fetch else False

    return {
        'mode':        mode,
        'goal':        goal,
        'reading':     reading,
        'concepts':    total_c,
        'top_concept': top_concept,
        'fetching':    fetching,
        'ts':          datetime.now().strftime('%H:%M:%S'),
    }


def api_goal_compile(goal: str) -> dict:
    """Decompose a goal into structured objectives via Ollama, then inject into UMBER."""
    import yaml, requests as _req
    try:
        cfg = yaml.safe_load((ROOT / 'config.yaml').read_text())
        model    = cfg['model'].get('name', 'qwen2.5:1.5b')
        endpoint = cfg['model']['endpoint']

        system = (
            'You are a goal compiler. Decompose the user goal into 3-5 concrete objectives. '
            'Return ONLY valid JSON: {"goal":"...","objectives":["...","..."],'
            '"first_action":"...","mode":"focused|exploratory|analytical","confidence":0.0}'
        )
        payload = {
            'model': model,
            'prompt': f'[SYSTEM]\n{system}\n\n[USER]\n{goal}',
            'stream': False,
            'keep_alive': -1,
            'num_predict': 300,
            'format': 'json',
        }
        r = _req.post(endpoint, json=payload, timeout=30)
        result = json.loads(r.json()['response'])

        # Inject goal activation into UMBER via learner if running
        try:
            from learner import AutoLearner
            al = AutoLearner.get()
            if al._umber:
                slug = re.sub(r'[^a-z0-9_]', '_', goal[:40].lower().strip())
                al._umber.inject(slug, 0.8)
        except Exception:
            pass

        return result
    except Exception as e:
        return {
            'goal': goal,
            'objectives': [goal],
            'first_action': 'Research the topic to build context.',
            'mode': 'exploratory',
            'confidence': 0.5,
            'error': str(e),
        }


def api_embed_image(b64: str, label: str) -> dict:
    """Decode a base64 image, embed with multimodal, insert into graph."""
    import base64, tempfile, os
    try:
        data = base64.b64decode(b64)
        suffix = '.jpg'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            f.write(data); tmp = f.name
        try:
            sys.path.insert(0, str(ROOT / 'src'))
            from multimodal import embed_image
            from extractor  import Concept
            vec = embed_image(tmp)
            if vec is None:
                return {'ok': False, 'error': 'embed_image returned None'}
            concept = Concept(text=label[:200], embedding=vec, source='image')
            learner = __import__('learner').AutoLearner.get()
            if learner._index:
                learner._index.update([concept])
                learner._index.save()
            if learner._field:
                learner._field.ingest([concept], [''])
            return {'ok': True, 'label': label[:80]}
        finally:
            try: os.unlink(tmp)
            except Exception: pass
    except Exception as e:
        return {'ok': False, 'error': str(e)}


_graph3d_cache: dict = {'data': None, 'ts': 0.0, 'n': 0}

def api_graph3d(max_nodes: int = 5000) -> dict:
    """
    Top-N concepts from EmbeddingIndex pre-positioned for sigma.js.
    Uses JAM field activation for node ranking + k-NN similarity for edges.
    Cache: 60s.
    """
    import math as _math
    global _graph3d_cache

    now = time.monotonic()
    if _graph3d_cache['data'] and _graph3d_cache['n'] == max_nodes and now - _graph3d_cache['ts'] < 60:
        return _graph3d_cache['data']

    try:
        from embed_index import EmbeddingIndex
        index = EmbeddingIndex.get()

        HARD_CAP = 5000
        display_limit = min(max_nodes, HARD_CAP)

        # Get top-N texts by JAM field activation (or just index order if field unavailable)
        field_data: dict = {}    # text → full props dict
        try:
            from jam_field import JamField
            field = JamField.get()
            for text, props in field.top(display_limit, by='activation'):
                field_data[text] = props
        except Exception:
            pass

        all_texts = index.texts
        total     = len(all_texts)

        if field_data:
            ranked = sorted(
                [(t, field_data.get(t, {}).get('activation', 0.0)) for t in all_texts],
                key=lambda x: -x[1]
            )[:display_limit]
        else:
            ranked = [(t, 1.0) for t in all_texts[:display_limit]]

        n     = len(ranked)
        scale = max(300, _math.sqrt(n) * 18)
        nodes = []
        text_to_rank: dict[str, int] = {}
        for rank, (text, act) in enumerate(ranked):
            text_to_rank[text] = rank
            t      = rank / max(n - 1, 1)
            angle  = t * 2 * _math.pi * max(3, n // 50)
            radius = scale * (0.1 + 0.9 * t)
            size   = max(2, min(9, _math.log1p(max(act, 0.001)) * 3 + 2))
            label  = text if len(text) <= 40 else text[:39] + '…'
            props  = field_data.get(text, {})
            nodes.append({
                'id':         rank,
                'label':      label,
                'x':          round(radius * _math.cos(angle), 1),
                'y':          round(radius * _math.sin(angle), 1),
                'size':       round(size, 1),
                'activation': round(float(props.get('activation',  act)),  3),
                'ambiguity':  round(float(props.get('ambiguity',   0.0)),  3),
                'tension':    round(float(props.get('tension',     0.0)),  3),
                'coherence':  round(float(props.get('coherence',   0.0)),  3),
                'novelty':    round(float(props.get('novelty',     0.0)),  3),
                'momentum':   round(float(props.get('momentum',    0.0)),  3),
                'stability':  round(float(props.get('stability',   0.0)),  3),
                'persistence':round(float(props.get('persistence', 0.0)),  3),
            })

        # Build edges from k-NN similarity — only between displayed nodes
        # Sample at most 400 nodes to build edges (avoid O(n^2) for large views)
        SIM_THRESHOLD = 0.45
        EDGE_SAMPLE   = min(400, n)
        links         = []
        seen_pairs: set[tuple] = set()
        for rank, (text, _) in enumerate(ranked[:EDGE_SAMPLE]):
            neighbours = index.knn_text(text, k=6)
            for nb_text, sim in neighbours:
                if sim < SIM_THRESHOLD:
                    continue
                nb_rank = text_to_rank.get(nb_text)
                if nb_rank is None:
                    continue
                pair = (min(rank, nb_rank), max(rank, nb_rank))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    links.append({'source': rank, 'target': nb_rank,
                                  'value': round(sim, 3)})

        result = {
            'nodes':       nodes,
            'links':       links,
            'total_nodes': total,
            'shown':       len(nodes),
            'clustered':   False,
        }
        _graph3d_cache = {'data': result, 'ts': now, 'n': max_nodes}
        return result
    except Exception as e:
        return {'nodes': [], 'links': [], 'error': str(e), 'total_nodes': 0, 'shown': 0}


def api_config():
    cfg_path = ROOT / 'data' / 'engine_config.json'
    try:    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    except Exception: cfg = {}

    def _proc_running(pid_file):
        try:
            pid = int((DATA / pid_file).read_text())
            r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                               capture_output=True, text=True, timeout=3)
            return 'cloudflared' in r.stdout.lower()
        except Exception:
            return False

    def _tunnel_url(log_file):
        try:
            log = (DATA / log_file).read_text(encoding='utf-8', errors='ignore')
            m = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', log)
            return m.group(0) if m else ''
        except Exception:
            return ''

    return {
        'cfg': cfg,
        'tunnel_running': _proc_running('tunnel.pid'),
        'tunnel_url':     _tunnel_url('tunnel.log'),
        'cf_exists':      (ROOT / 'cloudflared.exe').exists(),
    }


# ── Runner jobs ───────────────────────────────────────────────────────────────

_jobs: dict = {}
_job_lock = threading.Lock()

# ── Cached pipeline imports (loaded once at first use, reused forever) ─────────
_pipeline_cache: dict = {}
_pipeline_cache_lock = threading.Lock()

def _get_pipeline(model: str):
    """Return (cfg, index, extract_fn, detect_fn, build_fn, call_fn) — all cached."""
    import yaml
    with _pipeline_cache_lock:
        if 'extract' not in _pipeline_cache:
            from embed_index import EmbeddingIndex
            from extractor   import extract
            from detector    import detect_and_log
            from modulator   import build_prompt, call_llm
            _pipeline_cache['extract'] = extract
            _pipeline_cache['detect']  = detect_and_log
            _pipeline_cache['build']   = build_prompt
            _pipeline_cache['call']    = call_llm
            _pipeline_cache['index']   = EmbeddingIndex.get()

        cfg = yaml.safe_load((ROOT / 'config.yaml').read_text())
        cfg['model']['name'] = model
        return (
            cfg,
            _pipeline_cache['index'],
            _pipeline_cache['extract'],
            _pipeline_cache['detect'],
            _pipeline_cache['build'],
            _pipeline_cache['call'],
        )


def _feedback_async(response: str, index, job_id: str = '',
                    prompt: str = '', meta: dict = None) -> None:
    """Absorb LLM response into EmbeddingIndex + RuntimeFeedback in background."""
    try:
        extract_fn = _pipeline_cache.get('extract')
        detect_fn  = _pipeline_cache.get('detect')
        if not extract_fn or not response.strip():
            return
        resp_concepts = extract_fn(response)
        if resp_concepts:
            detect_fn(response, resp_concepts)
            index.update(resp_concepts)
            index.save()
        # Feed into RuntimeFeedback
        try:
            from runtime_feedback import RuntimeFeedback
            RuntimeFeedback.get().record(
                job_id        = job_id or 'anon',
                prompt        = prompt,
                response_text = response,
                meta          = meta or {},
            )
        except Exception:
            pass
    except Exception:
        pass


def _run_pipeline(job_id: str, prompt: str, model: str):
    import time as _t
    T = {}
    def _tick(label):
        T[label] = _t.perf_counter()
    try:
        _tick('start')
        cfg, ix, extract, detect_and_log, build_prompt, call_llm = _get_pipeline(model)
        _tick('imports')

        concepts = extract(prompt)
        _tick('extract')

        result = detect_and_log(prompt, concepts)
        _tick('detect')

        mod = build_prompt(concepts, result, index=ix)
        ix.update(concepts); ix.save()
        _tick('graph')

        response = call_llm(prompt, mod.system_prompt, cfg)
        _tick('llm')

        # Fire-and-forget: absorb response into index + RuntimeFeedback
        _fb_meta = {
            'concepts_in':  [c.text for c in concepts],
            'ambiguity_in': result.score if result else 0.3,
            'llm_ms':       round((T['llm'] - T['graph']) * 1000) if 'llm' in T and 'graph' in T else 0,
            'strategy':     mod.level if mod else 'medium',
        }
        threading.Thread(
            target=_feedback_async,
            args=(response, ix, job_id, prompt, _fb_meta),
            daemon=True,
        ).start()
        _tick('feedback')

        def _ms(a, b): return round((T[b] - T[a]) * 1000)
        timing = {
            'imports_ms': _ms('start',   'imports'),
            'extract_ms': _ms('imports', 'extract'),
            'detect_ms':  _ms('extract', 'detect'),
            'graph_ms':   _ms('detect',  'graph'),
            'llm_ms':     _ms('graph',   'llm'),
            'feedback_ms':_ms('llm',     'feedback'),
            'total_ms':   _ms('start',   'feedback'),
        }

        with _job_lock:
            _jobs[job_id] = {
                'status': 'done',
                'result': {
                    'concepts': [{'text': c.text, 'source': c.source} for c in concepts],
                    'ambiguity': {'score': result.score, 'level': result.level,
                                  'variance': result.variance, 'cluster': result.cluster,
                                  'bridge': result.bridge},
                    'modulation': {'level': mod.level, 'neighbours': mod.neighbours,
                                   'meta_concepts': mod.meta_concepts,
                                   'system_prompt': mod.system_prompt},
                    'response': response, 'model': model,
                    'resp_concepts': [],  # populated async
                    'timing': timing,
                }
            }
    except Exception as e:
        with _job_lock:
            _jobs[job_id] = {'status': 'error', 'result': {'error': str(e)}}


# ── Tunnel helpers ────────────────────────────────────────────────────────────

_CF_BIN = ROOT / 'cloudflared.exe'


def _start_tunnel(port: int, pid_file: Path, log_file: Path) -> str:
    if not _CF_BIN.exists():
        return 'cloudflared.exe not found'
    _stop_tunnel(pid_file)
    log_file.write_text('', encoding='utf-8')
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == 'nt' else 0
    fh = open(log_file, 'w', encoding='utf-8')
    proc = subprocess.Popen(
        [str(_CF_BIN), 'tunnel', '--url', f'http://localhost:{port}'],
        stdout=fh, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True,
    )
    fh.close()
    pid_file.write_text(str(proc.pid))
    return ''


def _stop_tunnel(pid_file: Path):
    try:
        pid = int(pid_file.read_text())
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True, timeout=5)
    except Exception:
        pass
    try: pid_file.unlink()
    except Exception: pass


def _save_cfg(cfg: dict):
    path = ROOT / 'data' / 'engine_config.json'
    tmp  = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    tmp.replace(path)
    try:
        from config import Config
        Config.get_instance()._mtime = 0.0
    except Exception:
        pass


# ── Live feed page (public shareable) ────────────────────────────────────────

LIVE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ambiguity Engine — Live</title>
<meta property="og:title" content="Ambiguity Engine — Live AI Thinking">
<meta property="og:description" content="A local AI teaching itself from the internet in real time. Watch concepts form, collide, and evolve.">
<style>
*{box-sizing:border-box;margin:0;padding:0}
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500&display=swap');
body{background:#03030a;color:#8888b8;font-family:'JetBrains Mono',Consolas,monospace;
     min-height:100vh;overflow-x:hidden;font-size:13px}
/* ── header ── */
#hdr{padding:18px 28px;border-bottom:1px solid #0d0d1e;display:flex;
     align-items:center;gap:16px;position:sticky;top:0;z-index:10;
     background:rgba(3,3,10,0.92);backdrop-filter:blur(8px)}
.logo{font-size:13px;letter-spacing:0.35em;text-transform:uppercase;color:#4a9eff;font-weight:500}
.logo-sub{font-size:9px;color:#252540;letter-spacing:0.2em;text-transform:uppercase}
#pulse-dot{width:7px;height:7px;border-radius:50%;background:#44ff88;margin-left:auto;
           animation:pulse 2s ease-in-out infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(68,255,136,0.4)}
                 50%{opacity:0.6;box-shadow:0 0 0 6px rgba(68,255,136,0)}}
#hdr-mode{font-size:10px;letter-spacing:0.15em;text-transform:uppercase;color:#303050}
#hdr-goal{font-size:10px;color:#505078}
/* ── grid ── */
#grid{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:auto auto;
      gap:1px;background:#0a0a18;min-height:calc(100vh - 57px)}
.panel{background:#03030a;padding:20px 24px}
.panel-title{font-size:9px;letter-spacing:0.25em;text-transform:uppercase;
             color:#252540;margin-bottom:16px;border-bottom:1px solid #080816;
             padding-bottom:8px}
/* ── stat row ── */
.stat-row{display:flex;justify-content:space-between;align-items:baseline;
          padding:5px 0;border-bottom:1px solid #07071a}
.stat-row:last-child{border:none}
.sk{color:#404060;font-size:11px}
.sv{color:#b0b0d8;font-size:15px;font-weight:500}
.sv.green{color:#44ff88}.sv.blue{color:#4a9eff}.sv.amber{color:#f59e0b}.sv.red{color:#ef4444}
/* ── activity feed ── */
#feed-list{display:flex;flex-direction:column;gap:4px;max-height:320px;overflow:hidden}
.feed-item{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid #06060f;
           animation:fadein 0.4s ease;line-height:1.5}
@keyframes fadein{from{opacity:0;transform:translateX(-6px)}to{opacity:1;transform:none}}
.fi-time{color:#1e1e38;font-size:10px;flex-shrink:0;min-width:52px}
.fi-src{font-size:9px;flex-shrink:0;min-width:56px;letter-spacing:0.1em;text-transform:uppercase}
.fi-src.wikipedia{color:#4a9eff}.fi-src.reddit{color:#f59e0b}
.fi-src.openalex{color:#8b5cf6}.fi-src.arxiv{color:#ef4444}
.fi-src.web{color:#44ff88}.fi-src.gutenberg{color:#10b981}
.fi-title{color:#606080;font-size:11px;overflow:hidden;white-space:nowrap;
          text-overflow:ellipsis;flex:1}
.fi-n{color:#252540;font-size:10px;flex-shrink:0;min-width:36px;text-align:right}
/* ── concept cloud ── */
#concept-cloud{display:flex;flex-wrap:wrap;gap:7px;align-content:flex-start;
               min-height:180px;max-height:280px;overflow:hidden}
.concept-tag{font-size:11px;padding:3px 9px;border-radius:20px;
             border:1px solid;white-space:nowrap;transition:all 0.5s;letter-spacing:0.04em}
/* ── tension bar ── */
.tbar{margin-bottom:8px}
.tbar-label{display:flex;justify-content:space-between;margin-bottom:4px;font-size:10px;color:#404060}
.tbar-track{height:4px;background:#08081a;border-radius:2px;overflow:hidden}
.tbar-fill{height:100%;border-radius:2px;transition:width 0.8s ease}
/* ── bottom banner ── */
#banner{padding:12px 28px;border-top:1px solid #0a0a1e;font-size:10px;
        color:#1e1e38;letter-spacing:0.15em;text-transform:uppercase;
        display:flex;justify-content:space-between}
/* ── reading bar ── */
#reading-bar{grid-column:1/-1;background:#040410;border-top:1px solid #0a0a1e;
             padding:10px 24px;display:flex;gap:12px;align-items:center;min-height:40px}
#reading-label{font-size:9px;letter-spacing:0.2em;text-transform:uppercase;color:#1e1e38;flex-shrink:0}
#reading-text{font-size:12px;color:#404060;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
#reading-src{font-size:9px;letter-spacing:0.1em;text-transform:uppercase;margin-left:auto;flex-shrink:0}
@media(max-width:700px){#grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div id="hdr">
  <div>
    <div class="logo">◈ Ambiguity Engine</div>
    <div class="logo-sub">autonomous AI · teaching itself from the internet</div>
  </div>
  <div id="hdr-mode"></div>
  <div id="hdr-goal"></div>
  <div id="pulse-dot" title="live"></div>
</div>

<div id="grid">
  <!-- Stats -->
  <div class="panel">
    <div class="panel-title">Cognitive State</div>
    <div class="stat-row"><span class="sk">concepts learned</span><span class="sv blue" id="s-concepts">—</span></div>
    <div class="stat-row"><span class="sk">active right now</span><span class="sv green" id="s-active">—</span></div>
    <div class="stat-row"><span class="sk">field coherence</span><span class="sv" id="s-coh">—</span></div>
    <div class="stat-row"><span class="sk">entropy</span><span class="sv" id="s-entropy">—</span></div>
    <div class="stat-row"><span class="sk">mode</span><span class="sv amber" id="s-mode">—</span></div>
    <div class="stat-row"><span class="sk">goal</span><span class="sv" id="s-goal">—</span></div>
    <div class="stat-row"><span class="sk">articles read</span><span class="sv" id="s-articles">—</span></div>
  </div>

  <!-- Concept cloud -->
  <div class="panel">
    <div class="panel-title">Active Concepts</div>
    <div id="concept-cloud"></div>
  </div>

  <!-- Live feed -->
  <div class="panel">
    <div class="panel-title">Reading Now</div>
    <div id="feed-list"></div>
  </div>

  <!-- Field tensions -->
  <div class="panel">
    <div class="panel-title">Field Tensions</div>
    <div id="tension-bars"></div>
  </div>

  <!-- Reading bar -->
  <div id="reading-bar">
    <span id="reading-label">currently reading</span>
    <span id="reading-text">—</span>
    <span id="reading-src" class="fi-src"></span>
  </div>
</div>

<div id="banner">
  <span>ambiguity-engine · running locally · no cloud AI</span>
  <span id="ts">—</span>
</div>

<script>
let articleCount = 0;
const MODE_COL = {focused:'#f59e0b',exploratory:'#3b82f6',associative:'#8b5cf6',
                  conflicted:'#ef4444',saturated:'#8b5cf6',drifting:'#10b981',idle:'#303050'};
const SRC_LABELS = {wikipedia:'Wikipedia',reddit:'Reddit',openalex:'OpenAlex',
                    arxiv:'arXiv',web:'Web',gutenberg:'Gutenberg'};

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')}

async function tick(){
  try {
    const [st, field, cr] = await Promise.all([
      fetch('/api/state').then(r=>r.json()),
      fetch('/api/field').then(r=>r.json()),
      fetch('/api/state').then(r=>r.json()),
    ]);

    // Header
    const mode = st.mode||'idle';
    const goal = (st.goal||'explore').replace(/_/g,' ');
    document.getElementById('hdr-mode').textContent = mode;
    document.getElementById('hdr-mode').style.color = MODE_COL[mode]||'#505078';
    document.getElementById('hdr-goal').textContent = '· ' + goal;

    // Stats
    const f = st.field||{};
    set('s-concepts', (st.node_count||0).toLocaleString(), 'blue');
    set('s-active',   (f.active_count||0).toLocaleString(), 'green');
    set('s-coh',      (f.coherence||0).toFixed(4));
    set('s-entropy',  (st.entropy||0).toFixed(4));
    set('s-mode',     mode, 'amber');
    set('s-goal',     goal);
    set('s-articles', articleCount.toLocaleString());

    // Concept cloud
    const top = (f.top||[]).slice(0,40);
    if(top.length){
      const maxA = Math.max(...top.map(t=>typeof t[1]==='object'?t[1].activation||0:t[1]||0), 0.001);
      document.getElementById('concept-cloud').innerHTML = top.map(([txt,props])=>{
        const act = typeof props==='object'?props.activation||0:props||0;
        const t   = typeof props==='object'?props.tension||0:0;
        const pct = act/maxA;
        const size = Math.round(10 + pct*4);
        const op   = 0.3 + pct*0.7;
        const col  = t>0.5?'#ef4444':t>0.25?'#f59e0b':'#4a9eff';
        return `<span class="concept-tag" style="font-size:${size}px;border-color:${col};
          color:${col};opacity:${op.toFixed(2)}">${esc(txt)}</span>`;
      }).join('');
    }

    // Live feed
    const feed = (st.feed||[]).filter(e=>e.status==='ok'&&e.concepts>0).slice(-8).reverse();
    if(feed.length){
      articleCount = st.feed ? st.feed.filter(e=>e.status==='ok').length : articleCount;
      document.getElementById('feed-list').innerHTML = feed.map(e=>{
        const time = (e.ts||'').slice(11,19);
        const src  = e.source||'web';
        return `<div class="feed-item">
          <span class="fi-time">${time}</span>
          <span class="fi-src ${src}">${SRC_LABELS[src]||src}</span>
          <span class="fi-title">${esc((e.title||e.topic||'').slice(0,60))}</span>
          <span class="fi-n">+${e.concepts}</span>
        </div>`;
      }).join('');
    }

    // Tension bars
    const tensions = (f.top||[]).filter(([,p])=>typeof p==='object'&&(p.tension||0)>0.05)
      .sort((a,b)=>(b[1].tension||0)-(a[1].tension||0)).slice(0,6);
    if(tensions.length){
      document.getElementById('tension-bars').innerHTML = tensions.map(([txt,p])=>{
        const t=p.tension||0, c=p.coherence||0;
        const col=t>0.6?'#ef4444':t>0.35?'#f59e0b':'#4a9eff';
        return `<div class="tbar">
          <div class="tbar-label"><span>${esc(txt.slice(0,28))}</span><span style="color:${col}">${t.toFixed(3)}</span></div>
          <div class="tbar-track"><div class="tbar-fill" style="width:${(t*100).toFixed(1)}%;background:${col}"></div></div>
        </div>`;
      }).join('');
    }

    // Reading bar
    const cr2 = st.currently_reading||{};
    if(cr2.title){
      document.getElementById('reading-text').textContent = cr2.title;
      const src = cr2.source||'web';
      const srcEl = document.getElementById('reading-src');
      srcEl.textContent = SRC_LABELS[src]||src;
      srcEl.className = 'fi-src ' + src;
    }

    document.getElementById('ts').textContent = new Date().toLocaleTimeString();
  } catch(e){}
}

function set(id, val, cls){
  const el = document.getElementById(id);
  if(!el) return;
  el.textContent = val;
  el.className = 'sv' + (cls?' '+cls:'');
}

tick();
setInterval(tick, 3000);
</script>
</body>
</html>"""

# ── Embedded SPA HTML ─────────────────────────────────────────────────────────

VOICE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>AMBIGUITY ENGINE</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#070709;color:#b0b0d8;
          font-family:'Consolas','Courier New',monospace;overflow:hidden}
/* ── chat area ── */
#chat{position:absolute;top:0;left:0;right:0;bottom:72px;
      overflow-y:auto;padding:20px 16px 16px;display:flex;flex-direction:column;gap:12px}
#chat::-webkit-scrollbar{width:3px}
#chat::-webkit-scrollbar-track{background:transparent}
#chat::-webkit-scrollbar-thumb{background:#1a1a28}
/* ── bubbles ── */
.bubble{max-width:78%;line-height:1.65;font-size:13px;padding:10px 14px;border-radius:3px;word-break:break-word}
.user{align-self:flex-end;background:#0d0d1a;border:1px solid #222238;
      text-align:right;font-style:italic;color:#6868a0}
.engine{align-self:flex-start;background:#0a0a12;border:1px solid #1a1a28;color:#b0b0d8;white-space:pre-wrap}
.thinking{align-self:flex-start;color:#303050;font-size:11px;letter-spacing:0.12em;
          text-transform:uppercase;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
/* ── goal card ── */
.goal-card{align-self:flex-start;max-width:85%;background:#0a0a12;
           border:1px solid #1a2a4a;border-left:2px solid #4a9eff;padding:14px 16px;border-radius:3px}
.goal-card-title{font-size:9px;letter-spacing:0.22em;text-transform:uppercase;color:#4a9eff;margin-bottom:10px}
.goal-obj{font-size:12px;color:#9898c8;padding:5px 0;border-bottom:1px solid #111128;display:flex;align-items:flex-start;gap:8px}
.goal-obj:last-child{border-bottom:none}
.goal-obj-n{color:#303050;min-width:16px}
.goal-first{font-size:11px;color:#44ff88;margin-top:10px;letter-spacing:0.08em}
.goal-meta{font-size:9px;color:#303050;margin-top:8px;letter-spacing:0.12em;text-transform:uppercase}
/* ── feedback card ── */
.fb-card{align-self:flex-start;background:#0a0a12;border:1px solid #1a2820;
         border-left:2px solid #44ff88;padding:10px 14px;border-radius:3px;
         font-size:12px;color:#9898c8}
/* ── bottom bar ── */
#bar{position:fixed;bottom:0;left:0;right:0;height:72px;
     background:#09090f;border-top:1px solid #1a1a28;
     display:flex;align-items:center;gap:8px;padding:0 12px}
#txt{flex:1;background:#0d0d1a;border:1px solid #222238;color:#b0b0d8;
     font-family:inherit;font-size:13px;padding:10px 14px;border-radius:2px;
     outline:none;transition:border-color .2s}
#txt:focus{border-color:#2a2a4a}
#txt::placeholder{color:#303050}
#send{width:40px;height:40px;border-radius:50%;border:1px solid #222238;background:#0a0a12;
      cursor:pointer;display:flex;align-items:center;justify-content:center;outline:none;
      transition:border-color .2s,background .2s;flex-shrink:0}
#send:hover{border-color:#4a9eff;background:#0d0d1a}
#send svg{width:16px;height:16px;fill:#4a9eff}
.icon-btn{width:38px;height:38px;border-radius:50%;border:1px solid #1a1a28;background:transparent;
          cursor:pointer;display:flex;align-items:center;justify-content:center;outline:none;
          transition:border-color .2s;flex-shrink:0}
.icon-btn:hover{border-color:#333350}
.icon-btn svg{width:16px;height:16px;fill:#303050;transition:fill .2s}
.icon-btn:hover svg{fill:#6868a0}
#btn{width:40px;height:40px;border-radius:50%;border:1px solid #1a1a28;background:transparent;
     cursor:pointer;display:flex;align-items:center;justify-content:center;outline:none;
     transition:border-color .2s,box-shadow .2s;flex-shrink:0}
#btn.listening{border-color:#ff4444;box-shadow:0 0 12px #ff222244;animation:glow .9s ease-in-out infinite}
@keyframes glow{0%,100%{box-shadow:0 0 10px #ff222244}50%{box-shadow:0 0 20px #ff4444aa}}
#btn svg{width:16px;height:16px;transition:fill .2s}
#btn .mic-off{fill:#303050}
#btn .mic-on{fill:#ff4444;display:none}
#btn.listening .mic-off{display:none}
#btn.listening .mic-on{display:block}
/* ── interim ── */
#interim{position:fixed;bottom:72px;left:0;right:0;text-align:center;
         font-size:10px;color:#303050;padding:3px 16px;
         white-space:nowrap;overflow:hidden;text-overflow:ellipsis;background:#09090f}
/* ── echo pulse bubble ── */
.echo{align-self:flex-start;max-width:90%;font-size:10px;color:#404060;
      letter-spacing:0.06em;padding:5px 10px;border-left:2px solid #1a1a28;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
      transition:color .3s}
.echo.active{color:#5858a0;border-left-color:#2a2a50}
#pulse-btn.on svg{fill:#44ff88}
#pulse-btn.on{border-color:#1a3a1a}
video#cam{display:none}canvas#snap{display:none}
</style>
</head>
<body>
<div id="chat"></div>
<div id="interim"></div>
<video id="cam" autoplay playsinline></video>
<canvas id="snap"></canvas>
<input type="file" id="upload" accept="image/*" style="display:none" onchange="onUpload(this)">

<div id="bar">
  <button class="icon-btn" onclick="captureCamera()" title="Watch">
    <svg viewBox="0 0 24 24"><path d="M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm0-2a5 5 0 1 1 0 10A5 5 0 0 1 12 7zM1.5 6A1.5 1.5 0 0 1 3 4.5h2.4l1.05-1.57A1.5 1.5 0 0 1 7.7 2.25h8.6c.48 0 .93.23 1.22.68L18.6 4.5H21A1.5 1.5 0 0 1 22.5 6v13A1.5 1.5 0 0 1 21 20.5H3A1.5 1.5 0 0 1 1.5 19V6z"/></svg>
  </button>
  <input id="txt" type="text" placeholder="ask or start with Build, Create, Learn…" autocomplete="off"
         onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendText()}">
  <button id="send" onclick="sendText()" title="Send">
    <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
  </button>
  <button class="icon-btn" onclick="document.getElementById('upload').click()" title="Taste">
    <svg viewBox="0 0 24 24"><path d="M19 13a1 1 0 0 1 1 1v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-5a1 1 0 0 1 2 0v5h12v-5a1 1 0 0 1 1-1zM12 2a1 1 0 0 1 .707.293l4 4a1 1 0 0 1-1.414 1.414L13 5.414V15a1 1 0 0 1-2 0V5.414L8.707 7.707A1 1 0 0 1 7.293 6.293l4-4A1 1 0 0 1 12 2z"/></svg>
  </button>
  <button id="pulse-btn" class="icon-btn" onclick="togglePulse()" title="Echo pulse">
    <svg viewBox="0 0 24 24"><path d="M2 12h3l2-7 4 14 3-9 2 4h6"/></svg>
  </button>
  <button id="btn" onclick="toggle()" title="Sleep / Wakeup">
    <svg viewBox="0 0 24 24">
      <path class="mic-off" d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm6.5 9a.5.5 0 0 1 .5.5A7 7 0 0 1 12.5 17v2.5H15a.5.5 0 0 1 0 1H9a.5.5 0 0 1 0-1h2.5V17A7 7 0 0 1 5 10.5a.5.5 0 0 1 1 0 6 6 0 0 0 12 0 .5.5 0 0 1 .5-.5z"/>
      <path class="mic-on"  d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm6.5 9a.5.5 0 0 1 .5.5A7 7 0 0 1 12.5 17v2.5H15a.5.5 0 0 1 0 1H9a.5.5 0 0 1 0-1h2.5V17A7 7 0 0 1 5 10.5a.5.5 0 0 1 1 0 6 6 0 0 0 12 0 .5.5 0 0 1 .5-.5z"/>
    </svg>
  </button>
</div>
<script>
const chat    = document.getElementById('chat');
const btn     = document.getElementById('btn');
const interim = document.getElementById('interim');

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if(!SR){ addBubble('engine','Speech recognition not supported in this browser. Use Chrome or Edge.'); }

let rec = null, listening = false;

function toggle(){
  if(listening) stop(); else start();
}
function _setLabel(active){
  const l=document.getElementById('btn-label');
  if(l) l.textContent=active?'wakeup':'sleep';
}

function start(){
  if(!SR) return;
  rec = new SR();
  rec.lang = 'en-US';
  rec.continuous = false;
  rec.interimResults = true;

  rec.onstart = ()=>{
    listening = true;
    btn.classList.add('listening');
    _setLabel(true);
  };
  rec.onresult = e=>{
    let final='', inter='';
    for(const r of e.results){
      if(r.isFinal) final += r[0].transcript;
      else          inter += r[0].transcript;
    }
    interim.textContent = inter;
    if(final.trim()) send(final.trim());
  };
  rec.onerror = e=>{
    if(e.error !== 'no-speech') addBubble('engine','Mic error: '+e.error);
    stop();
  };
  rec.onend = ()=>{ stop(); };
  rec.start();
}

function stop(){
  listening = false;
  btn.classList.remove('listening');
  _setLabel(false);
  interim.textContent = '';
  if(rec){ try{ rec.stop(); }catch(e){} rec=null; }
}

const GOAL_RE = /^(build|create|start|learn|make|design|develop|implement|set\s+up|launch|establish|plan|organize|write|generate|produce|train|teach|analyze|explore|research|investigate|find|acquire|understand|master|complete|improve|optimize|fix|solve|deploy|run|execute|automate|integrate|connect|configure|enable|add|migrate|refactor|test|validate|measure|track|monitor|audit)\b/i;

function sendText(){
  const inp = document.getElementById('txt');
  const t = inp.value.trim();
  if(!t) return;
  inp.value = '';
  send(t);
}

async function send(text){
  stop();
  addBubble('user', text);
  if(GOAL_RE.test(text)){
    await _sendGoal(text);
  } else {
    await _sendQuery(text);
  }
}

async function _sendGoal(text){
  const el = addBubble('thinking', 'compiling goal...');
  try{
    const d = await fetch('/api/goal/compile',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal: text})
    }).then(r=>r.json());
    el.className='';el.removeAttribute('class');
    _renderGoalCard(el, d);
    chat.scrollTop=chat.scrollHeight;
    // Feedback loop: acknowledge into engine after 3s if no correction
    setTimeout(()=>_goalFeedback(text, d), 3000);
  }catch(e){
    el.className='bubble engine';
    el.textContent='Error: '+e;
  }
}

function _renderGoalCard(el, g){
  const objs=(g.objectives||[]).map((o,i)=>
    `<div class="goal-obj"><span class="goal-obj-n">${i+1}.</span>${esc2(o)}</div>`
  ).join('');
  const first=g.first_action?`<div class="goal-first">next: ${esc2(g.first_action)}</div>`:'';
  const meta=g.mode?`<div class="goal-meta">mode: ${esc2(g.mode)} · confidence: ${(g.confidence||0).toFixed(2)}</div>`:'';
  el.outerHTML=`<div class="goal-card"><div class="goal-card-title">${esc2(g.goal||'goal')}</div>${objs}${first}${meta}</div>`;
}

async function _goalFeedback(text, g){
  try{
    await fetch('/api/goal/feedback',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal: text, confidence: g.confidence||0.7})
    });
  }catch(e){}
}

async function _sendQuery(text){
  const el = addBubble('thinking', 'thinking...');
  try{
    const res = await fetch('/api/runner/stream',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({prompt: text, model: ''})
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf='', response='', started=false;
    while(true){
      const {done,value} = await reader.read();
      if(done) break;
      buf += dec.decode(value,{stream:true});
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for(const part of parts){
        const lines = part.trim().split('\n');
        const ev  = lines.find(l=>l.startsWith('event:'))?.slice(6).trim();
        const raw = lines.find(l=>l.startsWith('data:'))?.slice(5).trim();
        if(!ev||!raw) continue;
        const d = JSON.parse(raw);
        if(ev==='token'){
          if(!started){ el.className='bubble engine'; started=true; }
          response += d;
          el.textContent = response;
          chat.scrollTop = chat.scrollHeight;
        } else if(ev==='done'){
          el.innerHTML = `<span>${esc2(response||'(no response)')}</span>`+
            `<div style="font-size:9px;color:#505078;margin-top:6px">${d.total_ms}ms total · llm ${d.llm_ms}ms</div>`;
          chat.scrollTop = chat.scrollHeight;
        } else if(ev==='error'){
          el.className='bubble engine';
          el.textContent='Error: '+d.error;
        }
      }
    }
  }catch(e){
    el.className='bubble engine';
    el.textContent='Error: '+e;
  }
}

function addBubble(type, text){
  const d = document.createElement('div');
  d.className = 'bubble '+type;
  d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  _anchorBar();
  return d;
}
function _anchorBar(){
  const bar = document.getElementById('bar');
  const im  = document.getElementById('interim');
  if(chat.children.length > 0){
    bar.classList.add('has-chat');
    if(im) im.classList.add('has-chat');
    chat.style.paddingBottom = '80px';
  }
}
function esc2(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── Image (camera + upload) ───────────────────────────────────────────────────
let camStream = null;

async function captureCamera(){
  const video = document.getElementById('cam');
  const canvas = document.getElementById('snap');
  if(!camStream){
    try{
      camStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
      video.srcObject = camStream;
      await new Promise(r=>video.onloadedmetadata=r);
    }catch(e){ addBubble('engine','Camera error: '+e.message); return; }
  }
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video,0,0);
  const b64 = canvas.toDataURL('image/jpeg',0.85).split(',')[1];
  embedImage(b64, 'camera');
}

function onUpload(input){
  const file = input.files[0];
  if(!file) return;
  input.value = '';
  const reader = new FileReader();
  reader.onload = e=>{
    const b64 = e.target.result.split(',')[1];
    embedImage(b64, file.name);
  };
  reader.readAsDataURL(file);
}

async function embedImage(b64, label){
  const el = addBubble('thinking', 'embedding image...');
  try{
    const d = await fetch('/api/image/embed',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({data: b64, label: label})
    }).then(r=>r.json());
    el.className='bubble engine';
    el.textContent = d.ok
      ? 'Image embedded into graph: '+d.label
      : 'Error: '+(d.error||'unknown');
    chat.scrollTop=chat.scrollHeight;
  }catch(e){
    el.className='bubble engine';
    el.textContent='Error: '+e;
  }
}

// ── Engine echo pulse (1-second heartbeat) ───────────────────────────────────
let _pulseOn   = false;
let _pulseTimer = null;
let _echoEl    = null;   // single live-updating bubble
let _prevText  = '';

function togglePulse(){
  _pulseOn = !_pulseOn;
  document.getElementById('pulse-btn').classList.toggle('on', _pulseOn);
  if(_pulseOn){
    _echoEl = null;   // will be created on first tick
    _pulseTimer = setInterval(_echoPoll, 1000);
    _echoPoll(); // immediate first tick
  } else {
    clearInterval(_pulseTimer);
    if(_echoEl){ _echoEl.className='echo'; _echoEl.textContent='— pulse off —'; }
  }
}

async function _echoPoll(){
  try{
    const d = await fetch('/api/echo').then(r=>r.json());
    const icon  = d.fetching ? '▸' : '○';
    const line  = `${d.ts}  ${icon}  ${d.mode} · ${d.goal} · ${d.reading}  [${d.concepts.toLocaleString()} concepts]`;
    if(line === _prevText) return;   // no change, skip DOM update
    _prevText = line;
    if(!_echoEl || !document.contains(_echoEl)){
      _echoEl = document.createElement('div');
      _echoEl.className = 'echo';
      chat.appendChild(_echoEl);
    }
    _echoEl.className = 'echo active';
    _echoEl.textContent = line;
    chat.scrollTop = chat.scrollHeight;
    setTimeout(()=>{ if(_echoEl) _echoEl.className='echo'; }, 400);
  }catch(e){}
}

// Focus text input on load
document.getElementById('txt').focus();
</script>
</body>
</html>"""

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AMBIGUITY ENGINE</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' fill='%23070709'/><text x='50%25' y='28' font-family='monospace' font-size='26' font-weight='700' fill='%23b0b0d8' text-anchor='middle'>M</text><circle cx='16' cy='4' r='4' fill='%2344bb55'/></svg>">
<style>
@font-face{font-family:'Share Tech Mono';font-style:normal;font-weight:400;
  src:url('/fonts/ShareTechMono.woff2') format('woff2')}
@font-face{font-family:'VT323';font-style:normal;font-weight:400;
  src:url('/fonts/VT323.woff2') format('woff2')}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;font-family:'Share Tech Mono','Consolas','Courier New',monospace}
body{background:#070709;color:#b0b0d8;min-height:100vh}
/* LCD display numbers — used on big values */
.lcd{font-family:'VT323','Share Tech Mono',monospace!important;letter-spacing:0.06em}
a{color:#4a9eff}
button{cursor:pointer;font-family:inherit}
#nav{position:sticky;top:0;z-index:9000;background:#09090f;border-bottom:1px solid #222238;
     display:flex;align-items:center;padding:0 20px;height:40px}
.nav-brand{font-size:13px;letter-spacing:0.2em;color:#505078;text-transform:uppercase;margin-right:20px}
.nav-tab{padding:0 14px;height:40px;line-height:40px;font-size:11px;letter-spacing:0.12em;
         text-transform:uppercase;color:#505078;border:none;background:none;
         border-bottom:2px solid transparent;transition:color .15s}
.nav-tab:hover{color:#9898c8}
.nav-tab.active{color:#b0b0d8;border-bottom-color:#4a9eff}
.nav-right{margin-left:auto;font-size:11px;color:#404060}
.page{display:none}
.page.active{display:block}
/* STATUS BAR — always visible, pinned below nav */
#sb{position:sticky;top:40px;z-index:8000;background:#0a0a12;border-bottom:1px solid #222238;
    padding:7px 24px;display:flex;align-items:center;gap:0;font-size:13px;flex-wrap:wrap;
    /* always rendered, not inside a .page */}
.sb-sep{color:#4a4a70;margin:0 12px}
.sb-dim{color:#7878a8}
.sb-val{color:#b0b0d8;font-family:'VT323','Share Tech Mono',monospace;font-size:14px;letter-spacing:0.06em}
.sb-right{margin-left:auto;color:#505078;font-size:11px}
.cursor{animation:blink 1.1s step-end infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
/* SPLASH */
#splash{position:fixed;top:0;left:0;width:100%;height:100%;z-index:99999;background:#070709;
  display:flex;align-items:center;justify-content:center;
  transition:opacity 0.9s ease}
#splash.out{opacity:0;pointer-events:none}
#splash-core{text-align:center;position:relative}
/* SVG mark — M drawn by stroke-dashoffset animation */
#splash-mark{margin:0 auto 24px;display:block;width:96px;height:112px}
#m-path{
  stroke-dasharray:220;stroke-dashoffset:220;
  animation:m-draw 1.1s cubic-bezier(0.4,0,0.2,1) 0.15s forwards}
@keyframes m-draw{to{stroke-dashoffset:0}}
/* Green dot — fades in after M finishes drawing */
#m-dot{opacity:0;animation:dot-pop 0.35s ease 1.2s both;transform-origin:30px 5px}
@keyframes dot-pop{0%{opacity:0;transform:scale(0.2)}60%{transform:scale(1.3)}100%{opacity:1;transform:scale(1)}}
/* Dot glow pulse after it appears */
#m-dot{animation:dot-pop 0.35s ease 1.2s both,dot-glow 2.2s ease-in-out 1.55s infinite}
@keyframes dot-glow{0%,100%{filter:drop-shadow(0 0 4px #44ff8888)}50%{filter:drop-shadow(0 0 14px #44ff88cc)}}
#splash-title{font-size:21px;letter-spacing:0.42em;color:#b0b0d8;text-transform:uppercase;
  animation:sfadeIn 0.7s 0.3s ease both}
#splash-sub{font-size:10px;letter-spacing:0.28em;color:#4a4a70;text-transform:uppercase;
  margin:6px 0 26px;animation:sfadeIn 0.6s 0.6s ease both}
@keyframes sfadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.sline{font-size:11px;letter-spacing:0.16em;color:#505078;margin:6px 0;
  opacity:0;animation:sline 0.3s ease forwards}
.sline.ok{color:#44ff88}
@keyframes sline{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
#splash-bar{width:210px;height:2px;background:#1a1a28;margin:20px auto 0;border-radius:1px;overflow:hidden}
#splash-fill{height:100%;width:0%;background:linear-gradient(90deg,#4a9eff,#44ff88);
  transition:width 0.18s linear}
/* SPEED KNOB */
#knob-wrap{display:flex;align-items:center;gap:7px;margin-right:10px;flex-shrink:0}
#speed-knob{width:32px;height:32px;border-radius:50%;cursor:pointer;position:relative;
  background:radial-gradient(circle at 38% 32%,#1e1e30,#08080f);
  border:2px solid #2a2a42;
  box-shadow:0 0 0 1px #0d0d1a,0 2px 6px rgba(0,0,0,0.7),inset 0 1px 2px rgba(255,255,255,0.04);
  transition:transform 0.28s cubic-bezier(0.34,1.56,0.64,1),box-shadow .2s;
  user-select:none}
#speed-knob:hover{box-shadow:0 0 0 1px #0d0d1a,0 0 10px rgba(74,158,255,0.2),inset 0 1px 2px rgba(255,255,255,0.04)}
/* Tick ring */
#speed-knob::before{content:'';position:absolute;inset:-3px;border-radius:50%;
  background:conic-gradient(
    #ffffff0a 0deg 3deg,transparent 3deg 12deg,
    #ffffff0a 12deg 15deg,transparent 15deg 24deg,
    #ffffff0a 24deg 27deg,transparent 27deg 36deg,
    #ffffff0a 36deg 39deg,transparent 39deg 48deg,
    #ffffff0a 48deg 51deg,transparent 51deg 360deg);
  pointer-events:none}
/* Pointer */
#knob-ptr{position:absolute;top:4px;left:50%;transform:translateX(-50%);
  width:2px;height:9px;border-radius:1px;
  background:linear-gradient(to bottom,#44ff88,#228844);
  box-shadow:0 0 5px #44ff8899;pointer-events:none}
#knob-label{font-size:9px;letter-spacing:0.14em;color:#404060;text-transform:uppercase;
  min-width:26px;text-align:left;transition:color .2s}
#knob-label.active{color:#44ff88}
/* CENTRAL */
#central{text-align:center;padding:36px 20px 28px;border-bottom:1px solid #222238}
#mode-glyph{font-size:68px;letter-spacing:10px;text-transform:uppercase;line-height:1;
  font-family:'VT323','Share Tech Mono',monospace}
#goal-text{font-size:15px;letter-spacing:6px;text-transform:uppercase;color:#7878a0;margin:10px 0 24px}
.diag{display:inline-grid;grid-template-columns:repeat(5,112px);gap:0 12px;text-align:center}
.diag-label{font-size:10px;letter-spacing:0.16em;color:#505078;text-transform:uppercase;display:block;margin-bottom:2px}
.diag-val{font-size:32px;color:#9090c0;line-height:1;font-family:'VT323','Share Tech Mono',monospace}
/* PANELS */
#panels{display:grid;grid-template-columns:1fr 1fr 1fr;border-bottom:1px solid #222238}
.panel{padding:16px 20px;border-right:1px solid #222238;overflow:hidden}
.panel:last-child{border-right:none}
.ptitle{font-size:10px;letter-spacing:0.2em;color:#505078;text-transform:uppercase;
        margin-bottom:9px;padding-bottom:4px;border-bottom:1px solid #222238}
.prow{display:flex;justify-content:space-between;font-size:13px;padding:2px 0;gap:8px}
.pkey{min-width:85px;flex-shrink:0;color:#6868a0}
.pval{color:#9898c8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.cr{display:flex;align-items:center;font-size:12px;padding:2px 0;color:#6868a0;gap:6px}
.cn{min-width:95px;overflow:hidden;white-space:nowrap;color:#7878a8}
/* TEL */
#tel{padding:7px 24px;background:#0a0a12;border-bottom:1px solid #222238;
     font-size:11px;display:flex;gap:0;align-items:center;flex-wrap:wrap}
.tl{color:#505078;text-transform:uppercase;font-size:10px;margin-right:4px}
.tv{color:#8080a8}
/* PIPELINE */
#pipeline{padding:12px 24px;background:#07070b;border-bottom:1px solid #181828}
.pipe-title{font-size:10px;letter-spacing:0.2em;color:#505078;text-transform:uppercase;
            border-bottom:1px solid #1a1a28;padding-bottom:4px;margin-bottom:10px}
.pipe-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 32px}
.ps{margin:2px 0;font-size:11px}
.pl{color:#505078;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;display:inline-block;min-width:76px}
/* STREAM */
#stream{padding:12px 24px 20px;background:#0a0a12}
.stream-title{font-size:10px;letter-spacing:0.2em;color:#606090;text-transform:uppercase;
              margin-bottom:7px;padding-bottom:4px;border-bottom:1px solid #222238}
.ev{font-size:11px;padding:1px 0;line-height:1.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* CR BAR */
#cr-bar{background:#050508;border-bottom:1px solid #181828;padding:5px 24px;
        font-size:10px;color:#505078;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* SECTION */
.sec{padding:18px 22px}
.sec-title{font-size:10px;letter-spacing:0.2em;color:#505078;text-transform:uppercase;
           border-bottom:1px solid #222238;padding-bottom:4px;margin-bottom:12px}
.page-head{font-size:18px;letter-spacing:0.2em;text-transform:uppercase;color:#b0b0d8;
           padding-bottom:9px;border-bottom:1px solid #222238;margin-bottom:14px}
.card{background:#0a0a12;border:1px solid #1a1a28;padding:12px 16px;margin-bottom:7px}
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:7px;margin-bottom:12px}
.met{background:#0a0a12;border:1px solid #1a1a28;padding:9px 11px}
.met-l{font-size:9px;letter-spacing:0.15em;text-transform:uppercase;color:#505078}
.met-v{font-size:20px;color:#9898c8;margin-top:2px}
/* FIELD bars */
.fr{display:flex;align-items:center;gap:9px;padding:2px 0;font-size:12px}
.fn{min-width:190px;color:#9898c8}
.fb{flex:1;height:6px;background:#0a0a12;border-radius:2px;overflow:hidden}
.ff{height:100%;border-radius:2px}
.fv{min-width:56px;text-align:right;color:#6868a0;font-size:11px}
/* INNER TABS */
.itabs{display:flex;border-bottom:1px solid #222238;padding:0 22px}
.itab{padding:7px 12px;font-size:10px;letter-spacing:0.1em;text-transform:uppercase;
      color:#505078;border:none;background:none;border-bottom:2px solid transparent;cursor:pointer}
.itab.active{color:#9898c8;border-bottom-color:#4a9eff}
.ipage{display:none;padding:14px 22px}
.ipage.active{display:block}
/* RUNNER */
.rw{padding:20px 22px}
textarea#prompt{width:100%;height:90px;background:#0a0a12;border:1px solid #222238;
                color:#b0b0d8;font-size:13px;padding:9px 12px;resize:vertical}
textarea#prompt:focus{outline:none;border-color:#4a4a70}
.run-row{display:flex;gap:9px;align-items:center;margin-top:9px}
select#model{background:#0a0a12;border:1px solid #222238;color:#9898c8;padding:5px 9px;font-size:11px}
button#run-btn{background:#1a1a30;border:1px solid #4a9eff;color:#4a9eff;padding:7px 20px;
              font-size:11px;letter-spacing:0.1em;text-transform:uppercase}
button#run-btn:hover{background:#22224a}
button#run-btn:disabled{opacity:.4;cursor:default}
#runner-out{margin-top:16px}
.rs-title{font-size:9px;letter-spacing:0.2em;text-transform:uppercase;color:#505078;
          border-bottom:1px solid #1a1a28;padding-bottom:3px;margin-bottom:7px}
.tag{display:inline-block;background:#0d0d18;border:1px solid #222238;color:#9898c8;
     padding:2px 8px;margin:2px 3px;font-size:11px}
.llm-r{background:#0a0a12;border:1px solid #1a1a28;padding:12px 16px;font-size:13px;
        color:#b0b0d8;line-height:1.7;white-space:pre-wrap;margin-top:7px}
.spin{color:#505078;font-size:11px;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
/* YTP */
.yw{padding:18px 22px}
.layer-t{font-size:9px;letter-spacing:0.25em;text-transform:uppercase;padding:7px 0 4px;
         margin-top:18px;border-bottom:1px solid #181828}
.sgrid{display:grid;grid-template-columns:1fr 1fr;gap:2px 28px;margin:12px 0}
.sr{margin-bottom:8px}
.sr label{display:block;font-size:9px;letter-spacing:0.1em;text-transform:uppercase;color:#6060a0;margin-bottom:3px}
.sr input[type=range]{width:100%;accent-color:#4a9eff}
.sv{font-size:11px;color:#9898c8;margin-left:5px}
.yb{background:#0a0a12;border:1px solid #222238;color:#9898c8;padding:6px 16px;
    font-size:10px;letter-spacing:0.1em;text-transform:uppercase;margin-top:7px}
.yb:hover{border-color:#4a9eff;color:#4a9eff}
.rr2{display:flex;align-items:center;gap:9px;padding:5px 0;border-bottom:1px solid #0f0f1a}
.rl2{font-size:10px;color:#505078;letter-spacing:0.1em;text-transform:uppercase;min-width:190px}
.rb2{flex:1;height:3px;background:#111120;border-radius:2px}
.rf2{height:3px;border-radius:2px;background:#4a9eff}
.rv2{font-size:10px;color:#9898c8;min-width:46px;text-align:right}
.badge{font-size:8px;letter-spacing:0.1em;color:#44ff88;border:1px solid #1a3a1a;background:#0a1a0a;padding:1px 3px}
/* SETTINGS */
.sw{padding:18px 22px}
.tun-row{display:flex;align-items:center;gap:10px;margin-bottom:7px}
.ae-btn{background:#0a0a12;border:1px solid #222238;color:#9898c8;padding:6px 14px;
        font-size:10px;letter-spacing:0.08em;text-transform:uppercase}
.ae-btn:hover{border-color:#4a9eff;color:#4a9eff}
.ae-btn.danger:hover{border-color:#ff4444;color:#ff4444}
input.ti{background:#0a0a12;border:1px solid #222238;color:#b0b0d8;padding:5px 9px;font-size:12px}
input.ti:focus{outline:none;border-color:#4a4a70}
select.si{background:#0a0a12;border:1px solid #222238;color:#9898c8;padding:5px 9px;font-size:11px}
.msg{padding:5px 10px;font-size:11px;margin:5px 0}
.msg.ok{color:#44ff88;border:1px solid #1a3a1a;background:#0a1a0a}
.msg.err{color:#ff6644;border:1px solid #3a1a1a;background:#1a0a0a}
/* TABLE */
.tbl{font-size:11px;width:100%;border-collapse:collapse}
.tbl th{font-size:9px;letter-spacing:0.12em;text-transform:uppercase;color:#505078;
        padding:4px 7px;border-bottom:1px solid #1a1a28;text-align:left;font-weight:400}
.tbl td{padding:3px 7px;border-bottom:1px solid #0f0f1a;color:#9898c8}
.tbl tr:last-child td{border-bottom:none}
pre.rc{background:#07070a;border:1px solid #1a1a28;padding:10px;font-size:10px;
       color:#6868a0;max-height:260px;overflow:auto}
#explain-overlay{display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.72);align-items:center;justify-content:center}
#explain-overlay.open{display:flex}
#explain-box{background:#0d0d18;border:1px solid #2a2a50;border-radius:6px;max-width:480px;width:90%;padding:24px 26px;position:relative}
#explain-box h3{font-size:13px;letter-spacing:0.18em;text-transform:uppercase;color:#b0b0d8;margin-bottom:6px}
#explain-box .ex-calc{font-size:11px;color:#505078;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px}
#explain-box p{font-size:12px;color:#9898c8;line-height:1.7;margin-bottom:8px}
#explain-box .ex-close{position:absolute;top:12px;right:14px;background:none;border:none;color:#404060;font-size:16px;cursor:pointer;padding:0}
#explain-box .ex-close:hover{color:#b0b0d8}
#explain-box .ex-val{font-size:22px;color:#4a9eff;letter-spacing:0.06em;margin-bottom:12px}
.xable{cursor:pointer;border-bottom:1px dotted #2a2a48}
.xable:hover{color:#b0b0d8;border-bottom-color:#4a9eff}
#summary-bar{background:#0a0a14;border:1px solid #1a1a28;border-radius:4px;margin:10px 22px 0;padding:10px 14px;font-size:10px;color:#505078;line-height:1.8;display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start}
#summary-bar .sb2-title{font-size:9px;text-transform:uppercase;letter-spacing:0.2em;color:#303050;margin-bottom:4px}
#summary-bar .sb2-item{white-space:nowrap}
#summary-bar .sb2-item span{color:#7878a8}
</style>
</head>
<body>
<!-- Explain modal -->
<div id="explain-overlay" onclick="if(event.target===this)closeExplain()">
  <div id="explain-box">
    <button class="ex-close" onclick="closeExplain()">✕</button>
    <div id="ex-title" style="font-size:13px;letter-spacing:0.18em;text-transform:uppercase;color:#b0b0d8;margin-bottom:4px"></div>
    <div id="ex-val" class="ex-val"></div>
    <div id="ex-calc" class="ex-calc"></div>
    <div id="ex-body" style="font-size:12px;color:#9898c8;line-height:1.7"></div>
  </div>
</div>
<!-- SPLASH SCREEN -->
<div id="splash">
  <div id="splash-core">
    <!-- SVG M: strokes draw in, then green dot pops at apex -->
    <svg id="splash-mark" viewBox="0 0 60 72" xmlns="http://www.w3.org/2000/svg">
      <path id="m-path" d="M8,68 L8,8 L30,36 L52,8 L52,68"
            fill="none" stroke="#b0b0d8" stroke-width="3.5"
            stroke-linecap="round" stroke-linejoin="round"/>
      <circle id="m-dot" cx="30" cy="5" r="5" fill="#44bb55"/>
    </svg>
    <div id="splash-title">Ambiguity Engine</div>
    <div id="splash-sub">cognitive field · self-teaching ai</div>
    <div id="splash-lines"></div>
    <div id="splash-bar"><div id="splash-fill"></div></div>
  </div>
</div>
<script>
(function(){
  const lines=[
    ['loading knowledge graph',''],
    ['warming JAM field',''],
    ['connecting 17 sources',''],
    ['calibrating embeddings',''],
    ['system online','ok'],
  ];
  const lc=document.getElementById('splash-lines');
  const fill=document.getElementById('splash-fill');
  const total=4400;
  const start=Date.now();
  // Progress bar
  const barTimer=setInterval(()=>{
    const pct=Math.min(100,Math.round((Date.now()-start)/total*100));
    fill.style.width=pct+'%';
    if(pct>=100)clearInterval(barTimer);
  },80);
  // Status lines timed across 4s
  const delays=[300,1100,2000,2900,3600];
  lines.forEach(([txt,cls],i)=>{
    setTimeout(()=>{
      const d=document.createElement('div');
      d.className='sline'+(cls?' '+cls:'');
      d.textContent=(cls==='ok'?'> ':'· ')+txt;
      lc.appendChild(d);
    },delays[i]);
  });
  // Pre-fetch data while splash plays
  fetch('/api/state').catch(()=>{});
  fetch('/api/field').catch(()=>{});
  // Fade out after 4.4s, remove after 5s
  setTimeout(()=>document.getElementById('splash').classList.add('out'),4400);
  setTimeout(()=>{const s=document.getElementById('splash');if(s)s.remove();},5300);
})();
</script>
<!-- Engine summary bar — live reading ticker -->
<div id="summary-bar" style="display:grid;grid-template-columns:auto 1fr auto;gap:0;padding:0;min-height:44px;align-items:stretch">
  <!-- What it's reading now -->
  <div style="padding:8px 18px;border-right:1px solid #1a1a28;display:flex;flex-direction:column;justify-content:center;min-width:120px">
    <div class="sb2-title">reading now</div>
    <div id="sb-cr-title" style="font-size:12px;color:#9898c8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:320px">—</div>
  </div>
  <!-- Rolling topic + source feed -->
  <div style="padding:8px 18px;overflow:hidden;display:flex;align-items:center;gap:10px">
    <span id="sb-cr-src" style="font-size:9px;letter-spacing:0.15em;text-transform:uppercase;color:#4a4a70;flex-shrink:0">—</span>
    <span style="color:#222238;flex-shrink:0">|</span>
    <span id="sb-cr-topic" style="font-size:10px;color:#6868a0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">—</span>
    <span style="color:#222238;flex-shrink:0;margin-left:auto">|</span>
    <span id="sb-cr-concepts" style="font-size:10px;color:#505078;flex-shrink:0;white-space:nowrap">— concepts</span>
  </div>
  <!-- Recent articles mini-list -->
  <div style="padding:6px 18px;border-left:1px solid #1a1a28;display:flex;flex-direction:column;justify-content:center;min-width:220px;max-width:300px">
    <div class="sb2-title" style="margin-bottom:3px">recent</div>
    <div id="sb-recent" style="display:flex;flex-direction:column;gap:1px"></div>
  </div>
</div>
<div id="nav">
  <!-- Vintage speed knob — left of nav -->
  <div id="knob-wrap" title="Drag up/down to control learner speed">
    <div id="speed-knob"><div id="knob-ptr"></div></div>
    <span id="knob-label">MED</span>
  </div>
  <!-- M logo -->
  <span style="position:relative;display:inline-block;width:22px;height:26px;margin-right:10px;vertical-align:middle;flex-shrink:0">
    <span style="position:absolute;bottom:0;left:50%;transform:translateX(-50%);font-size:22px;font-weight:700;font-family:Consolas,monospace;color:#b0b0d8;line-height:1;letter-spacing:-1px">M</span>
    <span style="position:absolute;top:1px;left:50%;transform:translateX(-50%);width:6px;height:6px;background:#44bb55;border-radius:50%;box-shadow:0 0 6px #44ff8866"></span>
  </span>
  <button class="nav-tab active" onclick="showPage('core',this)">Core</button>
  <button class="nav-tab" onclick="showPage('mind',this)">Mind</button>
  <button class="nav-tab" onclick="showPage('graph',this)">Graph</button>
  <button class="nav-tab" onclick="showPage('runner',this)">Runner</button>
  <button class="nav-tab" onclick="showPage('config',this)">Config</button>
  <span class="nav-right" id="nc"></span>
</div>

<!-- CORE: engine pulse + field state merged -->
<div id="page-core" class="page active">
  <div id="sb">
    <span id="sb-status" style="font-weight:bold;letter-spacing:0.14em"><span class="cursor">_</span></span>
    <span class="sb-sep">&#124;</span><span class="sb-dim">mode</span>&nbsp;<span id="sb-mode" class="sb-val"></span>
    <span class="sb-sep">&#124;</span><span class="sb-dim">goal</span>&nbsp;<span id="sb-goal" class="sb-val"></span>
    <span class="sb-sep">&#124;</span><span class="sb-dim">energy</span>&nbsp;<span id="sb-energy" class="sb-val"></span>
    <span class="sb-sep">&#124;</span><span class="sb-dim">coherence</span>&nbsp;<span id="sb-coh" class="sb-val"></span>
    <span class="sb-sep">&#124;</span><span class="sb-dim">nodes</span>&nbsp;<span id="sb-nodes" class="sb-val"></span>
    <span class="sb-sep">&#124;</span><span class="sb-dim">aiq</span>&nbsp;<span id="sb-aiq" class="sb-val"></span>
    <span class="sb-right" id="sb-time"></span>
  </div>
  <div id="cr-bar">reading: ...</div>
  <div id="central">
    <div id="mode-glyph" style="color:#555"></div>
    <div id="goal-text"></div>
    <div class="diag">
      <div><span class="diag-label">coherence</span><span class="diag-val" id="d-coh"></span></div>
      <div><span class="diag-label">ambiguity</span><span class="diag-val" id="d-amb"></span></div>
      <div><span class="diag-label">volatility</span><span class="diag-val" id="d-vol"></span></div>
      <div><span class="diag-label">pressure</span><span class="diag-val" id="d-pres" style="font-size:18px"></span></div>
      <div><span class="diag-label">aiq</span><span class="diag-val" id="d-aiq" style="font-size:22px"></span></div>
    </div>
  </div>
  <div id="panels">
    <div class="panel"><div class="ptitle">▸ input layer</div><div id="p-in"></div></div>
    <div class="panel"><div class="ptitle">▸ memory layer</div><div id="p-mem"></div></div>
    <div class="panel"><div class="ptitle">▸ output layer</div><div id="p-out"></div></div>
  </div>
  <div id="tel"></div>
  <div id="pipeline"><div class="pipe-title">pipeline efficiency</div><div id="pipe-inner">waiting for first cycle…</div></div>
  <!-- Field state folded into Core -->
  <div class="sec" style="margin-top:0;border-top:1px solid #1a1a28">
    <div class="sec-title" style="padding:10px 22px 0">field state</div>
    <div style="padding:0 22px">
      <div id="field-banner" class="card"></div>
      <div class="mgrid" id="field-metrics"></div>
      <div class="sec-title">concept activation</div>
      <div id="field-concepts"></div>
      <div class="sec-title" style="margin-top:14px">regulation scalars</div>
      <div class="mgrid" id="field-reg"></div>
    </div>
  </div>
  <div id="stream"><div class="stream-title">event stream</div><div id="evlist"></div></div>
</div>

<!-- MIND: Cognition + Goals -->
<div id="page-mind" class="page">
  <div style="padding:14px 22px 0"><div class="page-head" style="margin-bottom:0">MIND</div></div>
  <div class="itabs">
    <button class="itab active" onclick="showItab('cg-live',this)">Live</button>
    <button class="itab" onclick="showItab('cg-mem',this)">Memory</button>
    <button class="itab" onclick="showItab('cg-ep',this)">Episodes</button>
    <button class="itab" onclick="showItab('cg-contra',this)">Contradictions</button>
    <button class="itab" onclick="showItab('cg-abs',this)">Abstractions</button>
    <button class="itab" onclick="showItab('cg-wv',this)">Worldview</button>
    <button class="itab" onclick="showItab('cg-goals',this)">Goals</button>
  </div>
  <div id="cg-live" class="ipage active"><div id="cg-live-i">loading…</div></div>
  <div id="cg-mem"  class="ipage"><div id="cg-mem-i">loading…</div></div>
  <div id="cg-ep"   class="ipage"><div id="cg-ep-i">loading…</div></div>
  <div id="cg-contra" class="ipage"><div id="cg-contra-i">loading…</div></div>
  <div id="cg-abs"  class="ipage"><div id="cg-abs-i">loading…</div></div>
  <div id="cg-wv"   class="ipage"><div id="cg-wv-i">loading…</div></div>
  <div id="cg-goals" class="ipage" style="padding:0;overflow:hidden">
    <iframe src="/goals" style="width:100%;height:calc(100vh - 88px);border:none;background:#020209"></iframe>
  </div>
</div>

<!-- GRAPH: knowledge graph visualizer -->
<div id="page-graph" class="page" style="padding:0;overflow:hidden;position:relative">
  <div id="graph-toolbar" style="position:absolute;top:8px;left:12px;z-index:10;display:flex;gap:8px;align-items:center">
    <span style="font-size:10px;color:#505078;letter-spacing:0.15em">GRAPH</span>
    <span id="graph-stats" style="font-size:10px;color:#4a4a70"></span>
    <button onclick="loadGraph()" style="background:#0a0a12;border:1px solid #222238;color:#7878a8;font-family:Consolas,monospace;font-size:10px;padding:2px 8px;cursor:pointer">REFRESH</button>
    <select id="graph-n" onchange="loadGraph()" style="background:#0a0a12;border:1px solid #222238;color:#7878a8;font-family:Consolas,monospace;font-size:10px;padding:2px 4px">
      <option value="200" selected>200 nodes</option>
      <option value="500">500 nodes</option>
      <option value="1000">1k nodes</option>
      <option value="2000">2k nodes</option>
    </select>
  </div>
  <div id="graph-hover" style="position:absolute;bottom:16px;left:12px;z-index:10;pointer-events:none;display:none;background:rgba(7,7,9,0.96);border:1px solid #222238;border-radius:5px;padding:10px 14px;min-width:220px;max-width:320px;font-family:Consolas,'Courier New',monospace"></div>
  <canvas id="graph-canvas" style="width:100%;height:calc(100vh - 40px);background:#050508;display:block"></canvas>
</div>
<script>
(function(){
  let _nodes=[], _links=[], _anim=null;
  window._graphLoaded = false;
  let _scale=1, _ox=0, _oy=0, _drag=null, _dragging=null, _hover=null;

  function _canvasSize(){
    const c = document.getElementById('graph-canvas');
    // Use actual rendered size; fall back to window if hidden (offsetWidth=0)
    const W = c.offsetWidth  || window.innerWidth  || 1200;
    const H = c.offsetHeight || window.innerHeight - 40 || 700;
    if(c.width!==W||c.height!==H){ c.width=W; c.height=H; }
    return {W,H};
  }

  window.loadGraph = function(){
    const n = document.getElementById('graph-n').value;
    document.getElementById('graph-stats').textContent = 'loading…';
    fetch('/api/graph3d?n='+n).then(r=>r.json()).then(d=>{
      // Normalise nodes — server gives {id, label, x, y, size, activation, ...}
      _nodes = (d.nodes||[]).map(n=>({
        id:          n.id,
        text:        n.label||String(n.id),
        x:           n.x||0,
        y:           n.y||0,
        size:        n.size||3,
        _x:          n.x||0,
        _y:          n.y||0,
        activation:  n.activation  ?? null,
        ambiguity:   n.ambiguity   ?? null,
        tension:     n.tension     ?? null,
        coherence:   n.coherence   ?? null,
        novelty:     n.novelty     ?? null,
        momentum:    n.momentum    ?? null,
        stability:   n.stability   ?? null,
        persistence: n.persistence ?? null,
      }));
      // Build id→node map — use String(id) as key so 0 works correctly
      const nodeMap = {};
      _nodes.forEach(n=>{ nodeMap[String(n.id)] = n; });
      // Normalise links — source/target are numeric rank ids
      _links = (d.links||[]).map(l=>{
        const si = String(l.source?.id ?? l.source);
        const ti = String(l.target?.id ?? l.target);
        return {_s: nodeMap[si], _t: nodeMap[ti]};
      }).filter(l=>l._s&&l._t);
      window._graphLoaded = true;
      document.getElementById('graph-stats').textContent =
        _nodes.length+' nodes · '+_links.length+' edges · '+d.total_nodes+' total';
      _startDraw();
    }).catch(e=>{ document.getElementById('graph-stats').textContent='error: '+e; });
  };

  function _startDraw(){
    if(_anim) cancelAnimationFrame(_anim);
    function frame(){ _draw(); _anim=requestAnimationFrame(frame); }
    _anim = requestAnimationFrame(frame);
  }

  function _draw(){
    const {W,H} = _canvasSize();
    const canvas = document.getElementById('graph-canvas');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle='#050508'; ctx.fillRect(0,0,W,H);
    ctx.save();
    ctx.translate(W/2+_ox, H/2+_oy);
    ctx.scale(_scale, _scale);

    // Edges — very faint so nodes stay readable
    ctx.strokeStyle='rgba(50,50,100,0.22)'; ctx.lineWidth=0.6/Math.max(_scale,0.1);
    ctx.beginPath();
    _links.forEach(l=>{
      ctx.moveTo(l._s.x, l._s.y); ctx.lineTo(l._t.x, l._t.y);
    });
    ctx.stroke();

    // Nodes — no on-canvas labels; hover tooltip goes to #graph-hover div
    _nodes.forEach(n=>{
      const isHover = n===_hover;
      const r = isHover ? n.size+2 : n.size;
      let col;
      if(isHover)       col='#44ff88';
      else if(n.size>7) col='#4a9eff';
      else if(n.size>5) col='#2a5090';
      else if(n.size>3) col='#1e2a50';
      else              col='#141428';
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI*2);
      ctx.fillStyle=col; ctx.fill();
      if(isHover){ ctx.strokeStyle='#44ff8866'; ctx.lineWidth=1.5/_scale; ctx.stroke(); }
    });

    // Labels only when zoomed in enough — top nodes only
    if(_scale > 1.8){
      ctx.font=`${10/_scale}px Consolas,monospace`;
      ctx.fillStyle='#9898c8';
      const visible = _nodes.filter(n=>{
        const sx=(n.x*_scale)+(W/2+_ox), sy=(n.y*_scale)+(H/2+_oy);
        return sx>-50&&sx<W+50&&sy>-20&&sy<H+20;
      });
      visible.forEach(n=>{
        if(n.size>5||n===_hover){
          ctx.fillText(n.text.slice(0,32), n.x+n.size+2, n.y+3/_scale);
        }
      });
    }
    ctx.restore();
  }

  function _toGraph(ex,ey){
    const {W,H}=_canvasSize();
    return {x:(ex-(W/2+_ox))/_scale, y:(ey-(H/2+_oy))/_scale};
  }

  document.addEventListener('DOMContentLoaded',function(){
    const canvas=document.getElementById('graph-canvas');
    canvas.addEventListener('mousedown',e=>{
      _drag={ex:e.clientX,ey:e.clientY,ox:_ox,oy:_oy};
      const g=_toGraph(e.clientX,e.clientY);
      _dragging=_nodes.find(n=>Math.hypot(n.x-g.x,n.y-g.y)<12)||null;
    });
    canvas.addEventListener('mousemove',e=>{
      if(_drag&&!_dragging){ _ox=_drag.ox+(e.clientX-_drag.ex); _oy=_drag.oy+(e.clientY-_drag.ey); }
      if(_dragging){ const g=_toGraph(e.clientX,e.clientY); _dragging.x=g.x; _dragging.y=g.y; }
      const g=_toGraph(e.clientX,e.clientY);
      _hover=_nodes.find(n=>Math.hypot(n.x-g.x,n.y-g.y)<Math.max(14,n.size*1.5))||null;
      const hd=document.getElementById('graph-hover');
      if(!_hover){ hd.style.display='none'; return; }
      const n=_hover;
      function bar(val,color){
        if(val===null) return '<span style="color:#4a4a70">—</span>';
        const pct=Math.round(val*100);
        return `<span style="display:inline-block;width:72px;height:6px;background:#111120;border-radius:2px;vertical-align:middle;margin-left:6px"><span style="display:block;width:${pct}%;height:100%;background:${color};border-radius:2px"></span></span><span style="color:#6868a0;font-size:10px;margin-left:4px">${pct}</span>`;
      }
      function row(label,val,color){
        return `<div style="display:flex;justify-content:space-between;align-items:center;margin:3px 0"><span style="color:#505078;font-size:10px;letter-spacing:.1em;text-transform:uppercase;min-width:80px">${label}</span>${bar(val,color)}</div>`;
      }
      hd.innerHTML=`
        <div style="color:#b0b0d8;font-size:12px;font-weight:500;margin-bottom:8px;white-space:normal;line-height:1.4;word-break:break-word">${n.text}</div>
        <div style="border-top:1px solid #1a1a28;padding-top:7px">
          ${row('activation', n.activation,  '#4a9eff')}
          ${row('novelty',    n.novelty,     '#44ff88')}
          ${row('tension',    n.tension,     '#ff4444')}
          ${row('coherence',  n.coherence,   '#8b5cf6')}
          ${row('ambiguity',  n.ambiguity,   '#f59e0b')}
          ${row('momentum',   n.momentum,    '#06b6d4')}
          ${row('stability',  n.stability,   '#9898c8')}
          ${row('persistence',n.persistence, '#34d399')}
        </div>`;
      hd.style.display='block';
    });
    canvas.addEventListener('mouseup',()=>{ _drag=null; _dragging=null; });
    canvas.addEventListener('wheel',e=>{
      e.preventDefault();
      const f=e.deltaY>0?0.85:1.18;
      _scale=Math.max(0.05,Math.min(20,_scale*f));
    },{passive:false});
    window.addEventListener('resize',()=>_canvasSize());
  });
})();
</script>

<!-- RUNNER -->
<div id="page-runner" class="page">
  <div class="rw">
    <div class="page-head">RUNNER</div>
    <textarea id="prompt" placeholder="Type anything — a question, a fragment, a tension…"></textarea>
    <div class="run-row">
      <select id="model">
        <option>qwen2.5:3b-instruct</option>
        <option>qwen2.5:7b-instruct</option>
        <option>llama3.2:3b</option>
      </select>
      <button id="run-btn" onclick="runPipeline()">RUN</button>
      <span id="run-status"></span>
    </div>
    <div id="runner-out"></div>
  </div>
</div>

<!-- CONFIG: Learner + Settings + YTP -->
<div id="page-config" class="page">
  <div style="padding:14px 22px 0"><div class="page-head" style="margin-bottom:0">CONFIG</div></div>
  <div class="itabs">
    <button class="itab active" onclick="showConfigTab('cfg-learner',this)">Learner</button>
    <button class="itab" onclick="showConfigTab('cfg-settings',this);loadSettings()">Settings</button>
    <button class="itab" onclick="showConfigTab('cfg-ytp',this);loadYtp()">YTP</button>
  </div>
  <div id="cfg-learner" class="ipage active" style="padding:0;overflow:hidden">
    <iframe id="learner-frame" src="/learner" style="width:100%;height:calc(100vh - 88px);border:none;background:#07070a"></iframe>
  </div>
  <div id="cfg-settings" class="ipage">
    <div class="sw" style="padding-top:0">
      <div id="settings-inner">loading…</div>
    </div>
  </div>
  <div id="cfg-ytp" class="ipage">
    <div class="yw" style="padding-top:0">
      <div style="font-size:10px;color:#505078;margin-bottom:14px">layer 1 = physics (manual) · layer 2 = regulated (autonomous)</div>
      <div id="ytp-gate">checking access…</div>
    </div>
  </div>
</div>

<script>
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const f2  = (v,d=2) => (typeof v==='number'?v:parseFloat(v)||0).toFixed(d);

const MC = {focused:'#f59e0b',exploratory:'#3b82f6',associative:'#8b5cf6',
            exploitative:'#ef4444',reflective:'#10b981',conflicted:'#ef4444',
            saturated:'#8b5cf6',drifting:'#10b981'};
const mc = m => MC[m]||'#555';

function bar10(v,w=10){
  const f=Math.max(0,Math.min(w,Math.round(v*w)));
  return`<span style="color:#3a5a5a;letter-spacing:-1px">${'▮'.repeat(f)}</span><span style="color:#1a1a28;letter-spacing:-1px">${'▮'.repeat(w-f)}</span>`;
}
function ebar(v,w=10){
  const f=Math.max(0,Math.min(w,Math.round(v*w)));
  const c=v<0.3?'#ff4444':v<0.6?'#ffaa44':'#44ff88';
  return`<span style="color:${c};letter-spacing:-1px">${'▮'.repeat(f)}</span><span style="color:#141418;letter-spacing:-1px">${'▮'.repeat(w-f)}</span>`;
}

// Clock
setInterval(()=>{ document.getElementById('nc').textContent=new Date().toTimeString().slice(0,8); },1000);

// ── Speed Knob ────────────────────────────────────────────────────────────────
(function(){
  const LEVELS = ['IDLE','LOW','MED','HIGH','MAX'];
  const ANGLES = [-135,-67.5,0,67.5,135];
  const CFGS = [
    {cycle_time:45, n_workers:4,   topics_per_cycle:10},
    {cycle_time:15, n_workers:16,  topics_per_cycle:40},
    {cycle_time:0,  n_workers:64,  topics_per_cycle:80},
    {cycle_time:0,  n_workers:128, topics_per_cycle:150},
    {cycle_time:0,  n_workers:256, topics_per_cycle:200},
  ];
  let level = 2; // start at MED
  const knob  = document.getElementById('speed-knob');
  const label = document.getElementById('knob-label');

  function applyLevel(l){
    l = Math.max(0,Math.min(4,l));
    level = l;
    knob.style.transform = `rotate(${ANGLES[l]}deg)`;
    label.textContent = LEVELS[l];
    label.className = l===4?'active':'';
    fetch('/api/set_speed',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(CFGS[l])}).catch(()=>{});
  }

  // Drag up = faster, drag down = slower
  let dragY = null, lastDelta = 0;
  knob.addEventListener('mousedown', e=>{ dragY=e.clientY; e.preventDefault(); });
  document.addEventListener('mousemove', e=>{
    if(dragY===null) return;
    const dy = dragY - e.clientY;
    lastDelta += dy;
    dragY = e.clientY;
    if(lastDelta > 28){ applyLevel(level+1); lastDelta=0; }
    if(lastDelta <-28){ applyLevel(level-1); lastDelta=0; }
  });
  document.addEventListener('mouseup', ()=>{ dragY=null; lastDelta=0; });
  // Click cycles forward
  knob.addEventListener('click', ()=>applyLevel((level+1)%5));

  applyLevel(2); // init to MED without API call on load
  // Override the fetch on initial apply
  knob.style.transform=`rotate(${ANGLES[2]}deg)`;
})();

// Navigation
let curPage='core';
function showPage(name,btn){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  btn.classList.add('active');
  curPage=name;
  if(name==='mind') loadCognition();
  if(name==='graph' && !window._graphLoaded) loadGraph();
}
function showItab(id,btn){
  const par=btn.closest('.page');
  par.querySelectorAll('.itab').forEach(t=>t.classList.remove('active'));
  par.querySelectorAll('.ipage').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
function showConfigTab(id,btn){
  const par=btn.closest('.page');
  par.querySelectorAll('.itab').forEach(t=>t.classList.remove('active'));
  par.querySelectorAll('.ipage').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}

// ── CORE ─────────────────────────────────────────────────────────────────────
async function loadCore(){
  try{ const d=await fetch('/api/state').then(r=>r.json()); renderCore(d); }catch(e){}
}
function renderCore(d){
  const col=mc(d.mode);
  const sw=d.paused?'PAUSED':d.fetching?'FETCHING':'RUNNING';
  const sc=d.paused?'#ff4444':d.fetching?'#4a9eff':'#44ff88';
  const aq=d.aiq_cpm>20?'#44ff88':d.aiq_cpm>5?'#f59e0b':'#505078';

  const sbs=document.getElementById('sb-status');
  sbs.style.color=sc;
  sbs.innerHTML=sw+'<span class="cursor">_</span>'+(d.elapsed?` <span style="color:#7878a8;font-size:11px">${d.elapsed}</span>`:'');
  document.getElementById('sb-mode').textContent=d.mode; document.getElementById('sb-mode').style.color=col;
  document.getElementById('sb-goal').textContent=d.goal;
  document.getElementById('sb-energy').textContent=f2(d.energy);
  document.getElementById('sb-coh').textContent=f2(d.coherence,6);
  document.getElementById('sb-nodes').textContent=(d.n_nodes||0).toLocaleString();
  document.getElementById('sb-aiq').textContent=d.aiq; document.getElementById('sb-aiq').style.color=aq;
  document.getElementById('sb-time').textContent=d.date+'  '+d.now;

  const cr=d.currently_reading||{};
  if(cr.title) document.getElementById('cr-bar').textContent=
    'reading: '+cr.title+(cr.progress?' ['+cr.progress+']':'')+' · '+(cr.source||'');

  // Update summary bar with live reading info
  const srcColors={wikipedia:'#4a9eff',reddit:'#f59e0b',arxiv:'#ef4444',audio:'#44ff88',
                   image:'#8b5cf6',web:'#44cc88',openalex:'#8b5cf6',hackernews:'#f59e0b',
                   wikidata:'#4a9eff',conceptnet:'#10b981',wordnet:'#10b981',
                   pubmed:'#ef4444',crawl:'#44ff88',ddg_news:'#f59e0b',bing_web:'#9898c8'};
  if(cr.title){
    document.getElementById('sb-cr-title').textContent = cr.title;
    const srcEl = document.getElementById('sb-cr-src');
    srcEl.textContent = (cr.source||'—').toUpperCase();
    srcEl.style.color = srcColors[cr.source]||'#505078';
    document.getElementById('sb-cr-topic').textContent = cr.topic||cr.url||'—';
    document.getElementById('sb-cr-concepts').textContent = (cr.concepts_found!=null?cr.concepts_found+' concepts':'—');
  }
  // Recent articles mini-list (last 4 from feed)
  const recentEl = document.getElementById('sb-recent');
  if(recentEl && (d.feed||[]).length){
    recentEl.innerHTML = (d.feed||[]).slice(0,4).map(f=>{
      const c=srcColors[f.source]||'#4a4a70';
      return `<div style="display:flex;gap:6px;font-size:9px;overflow:hidden;white-space:nowrap"><span style="color:${c};flex-shrink:0">${(f.source||'').toUpperCase().slice(0,6)}</span><span style="color:#404060;overflow:hidden;text-overflow:ellipsis">${esc((f.title||'').slice(0,32))}</span></div>`;
    }).join('');
  }

  document.getElementById('mode-glyph').textContent=d.mode;
  document.getElementById('mode-glyph').style.color=col;
  document.getElementById('mode-glyph').style.textShadow=`0 0 100px ${col}44`;
  document.getElementById('goal-text').textContent=d.goal;
  document.getElementById('d-coh').textContent=f2(d.coherence,6);
  document.getElementById('d-amb').textContent=f2(d.ambiguity);
  document.getElementById('d-vol').textContent=f2(d.volatility);
  const pw=d.energy<0.3?'HIGH':d.energy<0.6?'MED':'LOW';
  const pc2=d.energy<0.3?'#ff4444':d.energy<0.6?'#ffaa44':'#3a3a4a';
  document.getElementById('d-pres').textContent=pw; document.getElementById('d-pres').style.color=pc2;
  document.getElementById('d-aiq').textContent=d.aiq; document.getElementById('d-aiq').style.color=aq;

  // Input panel
  let cr2='';
  for(const[n,v] of (d.top_concepts||[]).slice(0,8)){
    const s=n.length>14?n.slice(0,14)+'…':n;
    cr2+=`<div class="cr"><span class="cn">${esc(s)}</span>${bar10(Math.min(1,v))}<span style="color:#4a4a6a;font-size:11px">&nbsp;${f2(v)}</span></div>`;
  }
  if(!cr2) cr2='<div class="prow"><span class="pkey">signals</span><span class="pval">awaiting data</span></div>';
  const cc=d.unresolved_contradictions>0?'#ff6644':'#9898c8';
  document.getElementById('p-in').innerHTML=
    `<div class="prow"><span class="pkey">active signals</span><span class="pval">${(d.top_concepts||[]).length}</span></div>`+
    `<div class="prow"><span class="pkey">unresolved</span><span class="pval" style="color:${cc}">${d.unresolved_contradictions} contradictions</span></div>`+
    `<div class="prow"><span class="pkey">last trigger</span><span class="pval" style="font-size:9px">${esc((d.last_topic||'—').slice(0,24))}</span></div>`+
    `<div class="prow"><span class="pkey">source</span><span class="pval">${esc(d.last_source||'—')}</span></div>`+
    `<div style="margin-top:6px;border-top:1px solid #1a1a28;padding-top:5px">${cr2}</div>`;

  // Memory panel
  let ir='';
  for(const[k,v] of Object.entries(d.traits||{})){
    ir+=`<div class="cr"><span class="cn" style="min-width:95px">${esc(k.replace(/_/g,' ').slice(0,18))}</span>${bar10(Math.min(1,v))}<span style="color:#4a4a6a;font-size:11px">&nbsp;${f2(v)}</span></div>`;
  }
  document.getElementById('p-mem').innerHTML=
    `<div class="prow"><span class="pkey">nodes</span><span class="pval" style="color:${col}">${(d.n_nodes||0).toLocaleString()}</span></div>`+
    `<div class="prow"><span class="pkey">edges</span><span class="pval">${(d.n_edges||0).toLocaleString()}</span></div>`+
    `<div class="prow"><span class="pkey">sentences</span><span class="pval">${(d.n_sentences||0).toLocaleString()}</span></div>`+
    `<div class="prow"><span class="pkey">growth</span><span class="pval" style="color:#44ff88;opacity:.8">${d.growth}</span></div>`+
    `<div class="prow"><span class="pkey">learn rate</span><span class="pval" style="color:${aq}">${d.aiq}</span></div>`+
    `<div class="prow"><span class="pkey">articles</span><span class="pval">${d.aiq_aph} ingested</span></div>`+
    `<div style="margin-top:6px;border-top:1px solid #1a1a28;padding-top:5px"><div class="ptitle" style="margin-bottom:4px">identity traits</div>${ir}</div>`;

  // Output panel
  const ml=d.ambiguity>0.6?'high':d.ambiguity>0.3?'medium':'low';
  const mcc=ml==='high'?'#ef4444':ml==='medium'?'#f59e0b':'#555';
  const dc=d.denied_count>0?'#ff6644':'#9898c8';
  document.getElementById('p-out').innerHTML=
    `<div class="prow"><span class="pkey">intent</span><span class="pval" style="color:${col}">${esc(d.goal)}</span></div>`+
    `<div class="prow"><span class="pkey">action</span><span class="pval">${d.paused?'paused':d.fetching?'fetching':'learning'}</span></div>`+
    `<div class="prow"><span class="pkey">modulation</span><span style="color:${mcc};font-size:10px">${ml}</span></div>`+
    `<div class="prow"><span class="pkey">mode</span><span style="color:${col};font-size:10px">${esc(d.mode)}</span></div>`+
    `<div style="margin-top:6px;border-top:1px solid #1a1a28;padding-top:5px"><div class="ptitle" style="margin-bottom:4px">energy budget</div>`+
    `<div class="prow"><span class="pkey">level</span><span>${ebar(d.energy)}&nbsp;<span style="color:#353548">${f2(d.energy)}</span></span></div>`+
    `<div class="prow"><span class="pkey">spent</span><span class="pval">${d.spend_count} ops</span></div>`+
    `<div class="prow"><span class="pkey">denied</span><span class="pval" style="color:${dc}">${d.denied_count}</span></div>`+
    `<div class="prow" style="margin-top:4px"><span class="pkey">stability</span><span>${bar10(d.stability)}&nbsp;<span style="color:#252535">${f2(d.stability)}</span></span></div>`+
    `<div class="prow"><span class="pkey">novelty</span><span>${bar10(d.novelty)}&nbsp;<span style="color:#252535">${f2(d.novelty)}</span></span></div></div>`;

  // Telemetry
  const cv=parseFloat(d.cpu)||0; const gv=parseFloat(d.gpu)||0;
  const rc=cv>80?'#ff6644':cv<40?'#44ff88':'#8080a8';
  const gc=gv>80?'#ff6644':'#8080a8';
  document.getElementById('tel').innerHTML=
    `<span class="tl">CPU</span><span style="color:${rc}">${d.cpu}</span>`+
    `<span class="sb-sep">·</span><span class="tl">RAM</span><span class="tv">${d.ram}</span>`+
    `<span class="sb-sep">·</span><span class="tl">GPU</span><span style="color:${gc}">${d.gpu}</span>`+
    `<span class="sb-sep">·</span><span class="tl">TEMP</span><span class="tv">${d.temp}</span>`+
    `<span class="sb-sep" style="margin:0 16px">║</span>`+
    `<span class="tl">nodes</span><span class="tv">${(d.n_nodes||0).toLocaleString()}</span>`+
    `<span class="sb-sep">·</span><span class="tl">edges</span><span class="tv">${(d.n_edges||0).toLocaleString()}</span>`+
    `<span class="sb-sep">·</span><span class="tl">sentences</span><span class="tv">${(d.n_sentences||0).toLocaleString()}</span>`+
    `<span class="sb-sep">·</span><span class="tl">growth</span><span style="color:#44ff88;opacity:.75">${d.growth}</span>`;

  // Pipeline
  const p=d.perf||{}; const pa=p.process_avg_s||{}; const pp=p.process_pct||{}; const cyc=p.cycle||{};
  if(Object.keys(pa).length){
    const steps=['fetch','extract','detect','graph','save','subsys'];
    const labs=['fetch','extract (nlp)','detect','graph update','graph save','subsystems'];
    const bn=steps.reduce((a,b)=>(pp[a]||0)>(pp[b]||0)?a:b);
    let bh='';
    steps.forEach((s,i)=>{
      const pct=pp[s]||0; const sec=pa[s]||0; const isb=s===bn;
      const bw=Math.max(1,Math.round(pct*0.6));
      const c2=pct>40||isb?'#ef4444':pct>20?'#f59e0b':'#4a9eff';
      const bar='█'.repeat(bw)+'<span style="color:#1a1a28">'+'░'.repeat(60-bw)+'</span>';
      bh+=`<div class="ps"><span class="pl">${labs[i]}</span><span style="color:${c2};letter-spacing:-1px;font-size:8px">${bar}</span>&nbsp;<span style="color:${c2}">${sec.toFixed(2)}s</span>&nbsp;<span style="color:#383858">(${pct.toFixed(0)}%)</span>${isb?'&nbsp;<span style="color:#ef4444;font-size:10px">◀ bottleneck</span>':''}</div>`;
    });
    const eff=Math.round((cyc.items_ok||0)/Math.max(cyc.items_found||1,1)*100);
    document.getElementById('pipe-inner').innerHTML=
      `<div class="pipe-grid"><div>`+
      `<div style="font-size:9px;color:#404060;letter-spacing:0.1em;margin-bottom:5px">PER ARTICLE (avg ${p.n_samples||0})</div>${bh}`+
      `<div style="margin-top:5px;font-size:10px;color:#303050;border-top:1px solid #111120;padding-top:4px">total: <span style="color:#7878a8">${(pa.total||0).toFixed(2)}s</span></div></div>`+
      `<div><div style="font-size:9px;color:#404060;letter-spacing:0.1em;margin-bottom:5px">LAST CYCLE</div>`+
      `<div style="font-size:11px;line-height:2">`+
      `<div style="display:flex;justify-content:space-between"><span style="color:#505078">search phase</span><span style="color:#9898c8">${(cyc.t_search_s||0).toFixed(1)}s</span></div>`+
      `<div style="display:flex;justify-content:space-between"><span style="color:#505078">process (${cyc.workers||'—'} workers)</span><span style="color:#9898c8">${(cyc.t_process_s||0).toFixed(1)}s</span></div>`+
      `<div style="display:flex;justify-content:space-between;border-top:1px solid #1a1a28;margin-top:2px;padding-top:2px"><span style="color:#505078">total cycle</span><span style="color:#b0b0d8">${(cyc.t_total_s||0).toFixed(1)}s</span></div>`+
      `<div style="display:flex;justify-content:space-between;margin-top:5px"><span style="color:#505078">ok/found</span><span style="color:#44ff88">${cyc.items_ok||0}/${cyc.items_found||0}</span></div>`+
      `<div style="display:flex;justify-content:space-between"><span style="color:#505078">yield</span><span style="color:${eff>60?'#44ff88':'#f59e0b'}">${eff}%</span></div></div></div></div>`;
  }

  // Event stream
  let eh='';
  for(const e of (d.feed||[]).slice(0,50)){
    let td='——-——  ——:——:——'; let op=0.5;
    try{
      const dt=new Date(e.ts);
      const p2=n=>String(n).padStart(2,'0');
      td=`${p2(dt.getMonth()+1)}-${p2(dt.getDate())}  ${p2(dt.getHours())}:${p2(dt.getMinutes())}:${p2(dt.getSeconds())}`;
      op=Math.max(0.3,Math.min(1,1-(Date.now()-dt.getTime())/7200000));
    }catch(ex){}
    const nc2=e.concepts?'+'+e.concepts:'—';
    eh+=`<div class="ev" style="opacity:${op.toFixed(2)}">`+
      `<span style="color:#606090">[${td}]</span>`+
      `&nbsp;&nbsp;<span style="color:${col};display:inline-block;min-width:44px">${nc2.padStart(5)}</span>`+
      `&nbsp;&nbsp;<span style="color:#9090c0">${esc((e.title||e.topic||'').slice(0,55))}</span>`+
      `&nbsp;&nbsp;<span style="color:#686898">[${esc(e.source||'')}/${esc(e.topic||'')}]</span></div>`;
  }
  document.getElementById('evlist').innerHTML=eh||'<div class="ev" style="color:#1a1a24">awaiting first cycle…</div>';
}
setInterval(()=>{
  if(curPage==='core'){ loadCore(); loadField(); }
  if(curPage==='mind'){ loadCognition(); }
},4000);
loadCore(); loadField();

// ── FIELD ────────────────────────────────────────────────────────────────────
async function loadField(){
  try{
    const d=await fetch('/api/field').then(r=>r.json());
    const f=d.field||{}; const reg=d.reg||{}; const cog=d.cog||{};
    const mode=cog.mode||reg.mode||'associative';
    const goal=(cog.goal||reg.goal||'explore').replace(/_/g,' ');
    const col2=mc(mode);
    document.getElementById('field-banner').innerHTML=
      `<div style="display:flex;align-items:center;gap:10px">`+
      `<div><div style="font-size:14px;letter-spacing:0.15em;color:${col2};text-transform:uppercase">${mode}</div>`+
      `<div style="font-size:10px;color:#7878a8;margin-top:2px">drive: ${esc(goal)}</div></div>`+
      `<div style="margin-left:auto;text-align:right;font-size:10px;color:#505078">`+
      `<div>entropy <span style="color:#9898c8">${f2(reg.entropy||0,3)}</span></div>`+
      `<div>pressure <span style="color:#9898c8">${f2(reg.pressure||0,3)}</span></div>`+
      `<div>active <span style="color:#9898c8">${f.active_count||0}</span></div>`+
      `<div>concepts <span style="color:#9898c8">${f.field_size||0}</span></div></div></div>`;
    const fs=f.field_stats||{};
    document.getElementById('field-metrics').innerHTML=
      [['Mean Activation',fs.mean_activation,4],['Mean Novelty',fs.mean_novelty,4],['Mean Tension',fs.mean_tension,4],['Mean Stability',fs.mean_stability,4]]
      .map(([l,v,dp])=>`<div class="met"><div class="met-l">${l}</div><div class="met-v">${f2(v||0,dp)}</div></div>`).join('');
    const top=f.top||[]; const mx=top.length?top[0][1]||1:1;
    document.getElementById('field-concepts').innerHTML=top.map(([t,v])=>{
      const fr=v/Math.max(mx,0.001); const fc=fr>0.75?'#ef4444':fr>0.4?'#f59e0b':'#4a9eff';
      return`<div class="fr"><div class="fn">${esc(t)}</div><div class="fb"><div class="ff" style="width:${Math.round(fr*100)}%;background:${fc}"></div></div><div class="fv">${f2(v,5)}</div></div>`;
    }).join('')||'<div style="color:#505078;font-size:12px">No active concepts.</div>';
    document.getElementById('field-reg').innerHTML=
      [['Gain Rate',reg.gain_rate,4],['Decay Rate',reg.decay_rate,5],['Diffusion',reg.diffusion_strength,4],['Tick',reg.tick_count,0]]
      .map(([l,v,dp])=>`<div class="met"><div class="met-l">${l}</div><div class="met-v">${dp===0?Math.round(v||0):f2(v||0,dp)}</div></div>`).join('');
  }catch(e){}
}

// ── RUNNER ───────────────────────────────────────────────────────────────────
async function runPipeline(){
  const prompt=document.getElementById('prompt').value.trim();
  if(!prompt) return;
  const model=document.getElementById('model').value;
  const btn=document.getElementById('run-btn');
  btn.disabled=true;
  document.getElementById('run-status').innerHTML='<span class="spin">thinking…</span>';
  document.getElementById('runner-out').innerHTML='';

  let meta={}, response='', t0=Date.now();
  const llmBox=document.createElement('div');
  llmBox.style.cssText='font-family:inherit;font-size:13px;color:#b0b0d8;white-space:pre-wrap;line-height:1.7';

  try{
    const res=await fetch('/api/runner/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,model})});
    const reader=res.body.getReader();
    const dec=new TextDecoder();
    let buf='';
    while(true){
      const {done,value}=await reader.read();
      if(done) break;
      buf+=dec.decode(value,{stream:true});
      const parts=buf.split('\n\n');
      buf=parts.pop();
      for(const part of parts){
        const lines=part.trim().split('\n');
        const ev=lines.find(l=>l.startsWith('event:'))?.slice(6).trim();
        const raw=lines.find(l=>l.startsWith('data:'))?.slice(5).trim();
        if(!ev||!raw) continue;
        const d=JSON.parse(raw);
        if(ev==='meta'){
          meta=d;
          document.getElementById('run-status').innerHTML='<span class="spin">generating…</span>';

          // Graph RAG panel
          const rn=d.rag_nodes||[];
          const rs=d.rag_stats||{};
          const ragHtml=rn.length?
            `<div style="margin-bottom:14px">
              <div class="rs-title">2. graph retrieval
                <span style="color:#303050;font-weight:400;font-size:8px;margin-left:8px">
                  ${rs.n_nodes||0} concepts · ${rs.n_edges_used||0} edges · ${rs.retrieval_ms||0}ms
                </span>
              </div>
              <div style="margin-top:6px;display:flex;flex-direction:column;gap:4px">
              ${rn.map(n=>{
                const edgeStr=n.edges.map(e=>`<span style="color:#303050">${esc(e.target)}</span><span style="color:#1a1a28"> ${e.weight}</span>`).join('<span style="color:#1a1a28"> · </span>');
                return`<div style="padding:4px 8px;border-left:2px solid #0a1428;background:#06060f">
                  <span style="color:#2a6090;font-size:9px">${esc(n.text)}</span>
                  <span style="color:#1a3050;font-size:8px;margin-left:6px">sim ${n.sim}</span>
                  ${edgeStr?`<div style="margin-top:2px;font-size:8px">→ ${edgeStr}</div>`:''}
                </div>`;
              }).join('')}
              </div>
            </div>`
          :'<div style="margin-bottom:14px"><div class="rs-title">2. graph retrieval</div><div style="color:#303050;font-size:9px;padding:4px 0">no graph context — graph may be empty</div></div>';

          document.getElementById('runner-out').innerHTML=
            `<div style="margin-bottom:14px"><div class="rs-title">1. concepts extracted</div>`+
            `<div style="margin:6px 0">${(d.concepts||[]).map(c=>`<span class="tag">${esc(c)}</span>`).join('')||'<span style="color:#ff6644">None.</span>'}</div></div>`+
            ragHtml+
            `<div style="margin-bottom:14px"><div class="rs-title">3. llm output</div></div>`;
          document.getElementById('runner-out').appendChild(llmBox);
        } else if(ev==='token'){
          response+=d;
          llmBox.textContent=response;
        } else if(ev==='done'){
          document.getElementById('run-status').textContent='';
          btn.disabled=false;
          const timing={total_ms:d.total_ms,llm_ms:d.llm_ms,pre_ms:meta.pre_ms||0};
          document.getElementById('runner-out').innerHTML+=_timingBar(timing);
        } else if(ev==='error'){
          document.getElementById('runner-out').innerHTML=`<div class="msg err">Error: ${esc(d.error)}</div>`;
          btn.disabled=false;
        }
      }
    }
  }catch(e){
    document.getElementById('run-status').textContent='';
    document.getElementById('runner-out').innerHTML=`<div class="msg err">Error: ${esc(String(e))}</div>`;
    btn.disabled=false;
  }
}
function _pt(){} // no-op compat stub
function renderRunnerResult(r){
  const LC={low:'#44ff88',medium:'#ffaa44',high:'#ff4444'};
  const amb=r.ambiguity||{}; const lc=LC[amb.level]||'#9898c8';
  const mod=r.modulation||{};
  const tags=(r.concepts||[]).map(c=>`<span class="tag">${esc(c.text)}<span style="color:#505078;margin-left:4px;font-size:9px">${esc(c.source)}</span></span>`).join('');
  const nbrs=(mod.neighbours||[]).map(n=>`<span style="color:#7878a8">${esc(n)}</span>`).join(', ');
  const meta=(mod.meta_concepts||[]).map(c=>`<span style="color:#8b5cf6">${esc(c)}</span>`).join(', ');
  const rtags=(r.resp_concepts||[]).map(c=>`<span class="tag" style="color:#7878a8">${esc(c)}</span>`).join('');
  document.getElementById('runner-out').innerHTML=
    `<div style="margin-bottom:14px"><div class="rs-title">1. concepts extracted</div><div style="margin:6px 0">${tags||'<span style="color:#ff6644">None.</span>'}</div></div>`+
    `<div style="margin-bottom:14px"><div class="rs-title">2. ambiguity</div>`+
    `<div style="display:flex;align-items:center;gap:14px;margin:7px 0">`+
    `<div><div style="font-size:9px;color:#505078;letter-spacing:0.1em">SCORE</div><div style="font-size:28px;color:${lc}">${f2(amb.score||0,3)}</div></div>`+
    `<div style="font-size:16px;color:${lc};border:1px solid ${lc}44;padding:2px 10px">${(amb.level||'').toUpperCase()}</div>`+
    `<div style="display:flex;gap:7px">${['Variance','Cluster','Bridge'].map((l,i)=>`<div class="met"><div class="met-l">${l}</div><div class="met-v" style="font-size:14px">${f2([amb.variance,amb.cluster,amb.bridge][i]||0,3)}</div></div>`).join('')}</div></div></div>`+
    `<div style="margin-bottom:14px"><div class="rs-title">3. modulation</div>`+
    `<div style="font-size:11px;margin:5px 0"><span style="color:#505078;font-size:9px;text-transform:uppercase;letter-spacing:0.1em">Level</span>&nbsp;<span style="color:#9898c8">${esc(mod.level||'')}</span>&nbsp;&nbsp;<span style="color:#505078;font-size:9px;text-transform:uppercase;letter-spacing:0.1em">Model</span>&nbsp;<span style="color:#9898c8">${esc(r.model||'')}</span></div>`+
    (nbrs?`<div style="font-size:11px;margin:2px 0"><span style="color:#505078">neighbours</span> ${nbrs}</div>`:'')+
    (meta?`<div style="font-size:11px;margin:2px 0"><span style="color:#505078">meta pressure</span> ${meta}</div>`:'')+
    `<details style="margin-top:7px"><summary style="font-size:10px;color:#505078;cursor:pointer">System prompt</summary><pre style="font-size:10px;color:#6868a0;background:#07070a;border:1px solid #1a1a28;padding:8px;margin-top:5px;white-space:pre-wrap">${esc(mod.system_prompt||'')}</pre></details></div>`+
    `<div style="margin-bottom:14px"><div class="rs-title">4. llm output · ${esc(r.model||'')}</div><div class="llm-r">${esc(r.response||'(no response)')}</div></div>`+
    (rtags?`<div><div class="rs-title">5. response absorbed — ${(r.resp_concepts||[]).length} concepts</div><div style="margin:5px 0">${rtags}</div></div>`:'')+
    _timingBar(r.timing);
}
function _timingBar(t){
  if(!t) return '';
  const steps=[['imports',t.imports_ms],['extract',t.extract_ms],['detect',t.detect_ms],['graph',t.graph_ms],['llm',t.llm_ms],['feedback',t.feedback_ms]];
  const total=t.total_ms||1;
  const cols={'imports':'#505078','extract':'#4a9eff','detect':'#f59e0b','graph':'#44ff88','llm':'#8b5cf6','feedback':'#9898c8'};
  const bars=steps.map(([k,ms])=>{
    const pct=Math.max(1,Math.round((ms/total)*100));
    return`<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:10px">`+
      `<div style="width:70px;color:#6868a0;text-align:right">${k}</div>`+
      `<div style="flex:1;background:#0a0a12;height:10px;border-radius:2px">`+
        `<div style="width:${pct}%;background:${cols[k]||'#505078'};height:100%;border-radius:2px"></div></div>`+
      `<div style="width:55px;color:#9898c8;text-align:right">${ms}ms</div></div>`;
  }).join('');
  return`<div style="margin-top:14px"><div class="rs-title">timing — total ${total}ms</div>${bars}</div>`;
}

// ── COGNITION ────────────────────────────────────────────────────────────────
async function loadCognition(){
  try{ const d=await fetch('/api/cognition').then(r=>r.json()); renderCognition(d); }catch(e){}
}
function renderCognition(d){
  const f=d.field||{}; const reg=d.reg||{}; const cog=d.cog||{};
  const mode=cog.mode||reg.mode||'associative'; const col2=mc(mode);
  const fs=f.field_stats||{};
  // top entries are [text, {activation,...}] or [text, float] — normalise to [text, float]
  const rawTop=f.top||[];
  const top=rawTop.map(([t,v])=>[t, typeof v==='object'?(v.activation||0):Number(v)||0]);
  const mx=top.length?top[0][1]||0.001:0.001;
  const bars=top.filter(([t])=>t.length<=60&&!t.endsWith('.')).slice(0,20).map(([t,v])=>{
    const fr=v/Math.max(mx,0.001); const fc=fr>0.75?'#ef4444':fr>0.4?'#f59e0b':'#4a9eff';
    return`<div class="fr"><div class="fn">${esc(t)}</div><div class="fb"><div class="ff" style="width:${Math.round(fr*100)}%;background:${fc}"></div></div><div class="fv">${f2(v,5)}</div></div>`;
  }).join('');
  document.getElementById('cg-live-i').innerHTML=
    `<div class="mgrid">`+
    [`Mode:${(cog.mode||'').toUpperCase()}:${col2}:18`,`Goal:${(cog.goal||'').replace(/_/g,' ').toUpperCase()}:#a78bfa:18`,`Entropy:${f2(reg.entropy||0,3)}:#9898c8:20`,`Pressure:${f2(reg.pressure||0,3)}:#9898c8:20`,`Active:${f.active_count||0}:#9898c8:20`,`Mean Tension:${f2(fs.mean_tension||0,3)}:#9898c8:20`]
    .map(s=>{ const[l,v,c,fs2]=s.split(':'); return`<div class="met"><div class="met-l">${l}</div><div class="met-v" style="font-size:${fs2||20}px;color:${c}">${v}</div></div>`; }).join('')+
    `</div><div class="sec-title">top active concepts</div>${bars||'<div style="color:#505078">No data.</div>'}`;

  // Memory (top already normalised to [text, float] above)
  const ba=[...top].sort((a,b)=>(b[1]||0)-(a[1]||0)).slice(0,15);
  document.getElementById('cg-mem-i').innerHTML=
    `<div class="sec-title">Top by activation</div>`+
    `<table class="tbl"><thead><tr><th>Concept</th><th>Activation</th></tr></thead><tbody>`+
    ba.map(([t,v])=>`<tr><td>${esc(t)}</td><td>${f2(v,5)}</td></tr>`).join('')+
    `</tbody></table>`;

  // Episodes
  const paths=d.paths||[];
  const ep=d.episodes||[];
  document.getElementById('cg-ep-i').innerHTML=
    `<div class="sec-title">Strongest transitions (${paths.length} total)</div>`+
    `<table class="tbl"><thead><tr><th>From</th><th></th><th>To</th><th>Weight</th></tr></thead><tbody>`+
    paths.slice(0,20).map(([a,b,w])=>`<tr><td>${esc(a.slice(0,30))}</td><td style="color:#4a4a70">→</td><td>${esc(b.slice(0,30))}</td><td>${f2(w)}</td></tr>`).join('')+
    `</tbody></table>`+
    `<div class="sec-title" style="margin-top:12px">Recent episodes</div>`+
    `<table class="tbl"><thead><tr><th>Time</th><th>#</th><th>Amb</th><th>Path</th></tr></thead><tbody>`+
    ep.map(e=>`<tr><td>${(e.ts||'').slice(0,16)}</td><td>${(e.concepts||[]).length}</td><td>${f2(e.ambiguity||0)}</td><td style="color:#686898">${esc((e.concepts||[]).slice(0,5).join(' → ').slice(0,60))}</td></tr>`).join('')+
    `</tbody></table>`;

  // Contradictions
  const contra=d.contra||[];
  const open=contra.filter(c=>c.resolution_status==='open');
  document.getElementById('cg-contra-i').innerHTML=
    `<div class="mgrid">`+
    [['Total',contra.length,'#9898c8'],['Open',open.length,'#ff6644'],['Resolved',contra.length-open.length,'#44ff88']]
    .map(([l,v,c])=>`<div class="met"><div class="met-l">${l}</div><div class="met-v" style="color:${c}">${v}</div></div>`).join('')+
    `</div><div class="sec-title">Open contradictions</div>`+
    (open.length?open.map(c=>`<div class="card" style="border-color:#ff444428">`+
      `<div style="display:flex;align-items:center;gap:7px;font-size:12px">`+
      `<span style="color:#9898c8">${esc(c.concept_a||'')}</span><span style="color:#4a4a70">⟷</span><span style="color:#9898c8">${esc(c.concept_b||'')}</span>`+
      `<span style="margin-left:auto;font-size:10px;color:#505078">${c.conflict_type||''}</span></div>`+
      `<div style="font-size:10px;color:#505078;margin-top:3px">tension A=${f2(c.tension_a||0)} · B=${f2(c.tension_b||0)} · first ${(c.first_seen||'').slice(0,10)}</div></div>`
    ).join(''):'<div style="color:#44ff88;font-size:12px">No open contradictions.</div>');

  // Abstractions
  const abs=(d.abstractions||[]).sort((a,b)=>(b.emergence_score||0)-(a.emergence_score||0));
  document.getElementById('cg-abs-i').innerHTML=
    `<div class="mgrid">`+
    [['Abstract concepts',abs.length],['Stable (≥0.7)',abs.filter(a=>(a.stability||0)>=0.7).length]]
    .map(([l,v])=>`<div class="met"><div class="met-l">${l}</div><div class="met-v">${v}</div></div>`).join('')+
    `</div>`+
    (abs.length?abs.map(a=>{
      const st=a.stability||0; const sc=st>=0.7?'#44ff88':st>=0.4?'#f59e0b':'#888';
      const mem=(a.members||[]).slice(0,8).join(', ')+(a.members&&a.members.length>8?' …':'');
      return`<div class="card"><div style="font-size:12px;color:#8b5cf6">${esc(a.name||'~?')}</div>`+
        `<div style="font-size:10px;color:#505078;margin-top:2px">emergence ${f2(a.emergence_score||0,3)} · <span style="color:${sc}">stability ${f2(st,2)}</span> · seen ${a.reuse_frequency||0}×</div>`+
        `<div style="font-size:10px;color:#555;margin-top:2px">${esc(mem)}</div></div>`;
    }).join(''):'<div style="color:#505078">No abstractions yet.</div>');

  // Worldview
  const wv=d.worldview||{}; const pc=wv.persistent_concepts||[]; const sg=wv.surviving_goals||[]; const cc2=wv.chronic_contradictions||[];
  document.getElementById('cg-wv-i').innerHTML=
    (wv.identity_summary?`<div class="card" style="border-color:#4a9eff;margin-bottom:12px"><div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Identity Summary</div><div style="color:#ccc;font-size:12px;line-height:1.6">${esc(wv.identity_summary)}</div></div>`:'')+
    `<div style="font-size:10px;color:#505078;margin-bottom:10px">Last updated: ${wv.last_updated||'never'} · Update #${wv.update_count||0}</div>`+
    `<div class="sec-title">Persistent concepts (${pc.length})</div>`+
    (pc.length?`<table class="tbl"><thead><tr><th>Concept</th><th>Semantic Value</th></tr></thead><tbody>`+
      pc.slice(0,30).map(c=>`<tr><td>${esc(c.concept||'')}</td><td>${f2(c.semantic_value||0,4)}</td></tr>`).join('')+`</tbody></table>`:'<div style="color:#505078">None yet.</div>')+
    `<div class="sec-title" style="margin-top:12px">Surviving goals (${sg.length})</div>`+
    sg.map(g=>{const bw=Math.round((g.stability_score||0)*100);return`<div class="card"><div style="display:flex;justify-content:space-between"><span style="color:#a78bfa">${esc((g.goal||'').replace(/_/g,' '))}</span><span style="color:#777;font-size:10px">${g.recurrence||0}× fired</span></div><div style="height:3px;background:#1e2130;border-radius:2px;margin-top:6px"><div style="height:100%;width:${bw}%;background:#a78bfa;border-radius:2px"></div></div></div>`}).join('')+
    (cc2.length?`<div class="sec-title" style="margin-top:12px">Chronic contradictions</div>`+
      cc2.map(c=>`<div class="card" style="border-color:#ff444428"><div style="font-size:12px"><span style="color:#9898c8">${esc(c.concept_a||'')}</span><span style="color:#4a4a70;margin:0 6px">⟷</span><span style="color:#9898c8">${esc(c.concept_b||'')}</span><span style="float:right;color:#777;font-size:10px">${Math.round(c.age_hours||0)}h open</span></div></div>`).join(''):'');
}

// ── SETTINGS ─────────────────────────────────────────────────────────────────
async function loadSettings(){
  try{ const d=await fetch('/api/config').then(r=>r.json()); renderSettings(d); }catch(e){}
}
function renderSettings(d){
  const egos=(d.cfg&&d.cfg.egos)||{}; const ae=d.cfg&&d.cfg.active_ego||''; const en=Object.keys(egos);
  const raw=JSON.stringify(d.cfg||{},null,2);
  const tb=d.tunnel_running?`<button class="ae-btn danger" onclick="tunnelAction('stop')">Stop Tunnel</button>`:`<button class="ae-btn" onclick="tunnelAction('start')">Start Tunnel</button>`;
  const ti=d.tunnel_running?(d.tunnel_url?`<a href="${d.tunnel_url}" target="_blank" style="color:#44ff88">${d.tunnel_url}</a>`:'<span style="color:#505078">Starting…</span>'):'<span style="color:#505078">Free Cloudflare public URL (port 8501)</span>';
  const eOpts=['— none —',...en].map(n=>`<option value="${esc(n)}" ${n===ae?'selected':''}>${esc(n)}</option>`).join('');
  document.getElementById('settings-inner').innerHTML=
    `<div id="smsg"></div>`+
    `<div class="sec-title">Public Access</div><div class="tun-row">${tb}${ti}</div>`+
    (!d.cf_exists?'<div style="font-size:10px;color:#505078;margin-top:3px">cloudflared.exe not found in project root.</div>':'')+
    `<div class="sec-title" style="margin-top:18px">Ego</div>`+
    `<div style="display:flex;gap:7px;margin-bottom:7px"><select id="ego-sel" class="si">${eOpts}</select><button class="ae-btn" onclick="egoAction('load')">Load</button><button class="ae-btn danger" onclick="egoAction('delete')">Delete</button></div>`+
    (en.length<3?`<div style="display:flex;gap:7px;margin-bottom:7px"><input id="ego-name" class="ti" placeholder="New ego name…"><button class="ae-btn" onclick="egoAction('save')">Save Ego</button></div>`:'<div style="font-size:10px;color:#505078">Max 3 egos reached.</div>')+
    (en.length?`<div style="font-size:11px;color:#505078;margin-bottom:12px">Saved: ${en.map(n=>n===ae?`<strong style="color:#b0b0d8">${n}</strong>`:n).join(' · ')}</div>`:'')+
    `<div class="sec-title" style="margin-top:18px">Raw engine_config.json</div><pre class="rc">${esc(raw)}</pre>`;
}
async function tunnelAction(action){
  const msg=document.getElementById('smsg');
  msg.innerHTML='<div class="msg ok">Sending…</div>';
  try{
    await fetch('/api/tunnel/'+action+'/main',{method:'POST'});
    msg.innerHTML=`<div class="msg ok">${action==='start'?'Starting — public URL appears in ~8s':'Tunnel stopped.'}</div>`;
    setTimeout(loadSettings,9000);
  }catch(e){ msg.innerHTML=`<div class="msg err">${esc(String(e))}</div>`; }
}
async function egoAction(action){
  const sel=document.getElementById('ego-sel');
  const name=action==='save'?(document.getElementById('ego-name')?document.getElementById('ego-name').value.trim():''):sel.value;
  if(!name||name==='— none —') return;
  const msg=document.getElementById('smsg');
  try{
    const r=await fetch('/api/ego/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}).then(r=>r.json());
    msg.innerHTML=`<div class="msg ok">${esc(r.message||'Done.')}</div>`;
    setTimeout(loadSettings,500);
  }catch(e){ msg.innerHTML=`<div class="msg err">${esc(String(e))}</div>`; }
}

// ── YTP ──────────────────────────────────────────────────────────────────────
async function loadYtp(){
  try{
    const d=await fetch('/api/config').then(r=>r.json());
    if(!d.is_local){ document.getElementById('ytp-gate').innerHTML='<div style="text-align:center;padding:36px"><div style="color:#ff4444;letter-spacing:0.2em;text-transform:uppercase">access denied</div><div style="color:#404060;font-size:11px;margin-top:8px">YTP is only available on local access</div></div>'; return; }
    renderYtp(d.cfg||{});
  }catch(e){}
}
const L1=[
  ['energy','replenish_per_tick','Energy replenish / tick',0.01,0.30,0.08,0.01],
  ['energy','cost_exploration','Cost — exploration',0.01,0.30,0.06,0.01],
  ['energy','cost_simulation','Cost — simulation',0.01,0.25,0.04,0.01],
  ['energy','cost_region_switch','Cost — region switch',0.01,0.25,0.05,0.01],
  ['energy','cost_abstraction','Cost — abstraction',0.01,0.30,0.10,0.01],
  ['evolver','max_delta','Evolver max delta',0.01,0.20,0.05,0.01],
];
const L1_SOURCES=[
  ['source_weights','image','Image source weight',0,5,0.9,0.1],
  ['source_weights','audio','Audio source weight',0,5,0.9,0.1],
];
const L2=[
  ['attention','novelty_strength','Novelty strength',0,1],
  ['attention','bias_strength','Bias strength',0,1],
  ['meta_state','decay_rate','Decay rate',0.10,0.999],
  ['meta_state','reinforce_gain','Reinforce gain',0.05,0.70],
  ['meta_state','fatigue_k','Fatigue',0.05,0.85],
  ['meta_state','cooling_alpha','Cooling alpha',0,1],
  ['identity','drift_rate','Drift rate',0.001,0.08],
  ['goals','reduce_uncertainty','Reduce uncertainty',0,1],
  ['goals','increase_novelty','Increase novelty',0,1],
  ['goals','resolve_contradiction','Resolve contradiction',0,1],
  ['goals','maintain_stability','Maintain stability',0,1],
  ['goals','expand_regions','Expand regions',0,1],
];
function gv2(cfg,sec,key,def){ return (cfg[sec]&&cfg[sec][key]!=null)?cfg[sec][key]:def; }
function renderYtp(cfg){
  const reg=cfg._regulated||false;
  let l1='<div class="sgrid">';
  L1.forEach(([s,k,l,lo,hi,def,step])=>{
    const v=parseFloat(gv2(cfg,s,k,def));
    l1+=`<div class="sr"><label>${l}</label><input type="range" min="${lo}" max="${hi}" step="${step}" value="${v}" oninput="this.nextElementSibling.textContent=parseFloat(this.value).toFixed(2)" data-sec="${s}" data-key="${k}"><span class="sv">${v.toFixed(2)}</span></div>`;
  });
  l1+='</div>';
  let lsrc='<div class="sgrid">';
  L1_SOURCES.forEach(([s,k,l,lo,hi,def,step])=>{
    const v=parseFloat(gv2(cfg,s,k,def));
    lsrc+=`<div class="sr"><label>${l}</label><input type="range" min="${lo}" max="${hi}" step="${step}" value="${v}" oninput="this.nextElementSibling.textContent=parseFloat(this.value).toFixed(1)" data-sec="${s}" data-key="${k}"><span class="sv">${v.toFixed(1)}</span></div>`;
  });
  lsrc+='</div>';
  let l2=reg?L2.map(([s,k,l,lo,hi])=>{
    const v=parseFloat(gv2(cfg,s,k,0));
    const pct=Math.max(0,Math.min(100,Math.round((v-lo)/Math.max(hi-lo,1e-6)*100)));
    return`<div class="rr2"><div class="rl2">${l}</div><div class="rb2"><div class="rf2" style="width:${pct}%"></div></div><div class="rv2">${v.toFixed(4)}</div><div class="badge">AUTO</div></div>`;
  }).join(''):'<div style="color:#303050;font-size:11px;padding:9px 0">Regulator has not run yet — starts 60s after engine launch.</div>';
  document.getElementById('ytp-gate').innerHTML=
    `<div id="ymsg"></div>`+
    `<div class="layer-t" style="color:#4a9eff">Layer 1 — Physics Constants <span style="color:#303050;font-size:8px;margin-left:7px">manual · rarely changed</span></div>`+l1+
    `<div class="layer-t" style="color:#8b5cf6;margin-top:14px">Source Weights <span style="color:#303050;font-size:8px;margin-left:7px">0 = disabled · scale relative to text sources (max ~2.5)</span></div>`+lsrc+
    `<div style="display:flex;gap:7px;margin-top:10px"><button class="yb" onclick="applyPhysics()">Apply</button><button class="yb" onclick="resetPhysics()">Reset</button></div>`+
    `<div class="layer-t" style="color:#44ff88;margin-top:18px">Layer 2 — Adaptive Regulators <span style="color:#303050;font-size:8px;margin-left:7px">autonomous · every 60s</span></div>`+l2;
}
async function applyPhysics(){
  const updates={};
  document.querySelectorAll('#ytp-gate input[type=range]').forEach(s=>{
    const sec=s.dataset.sec; const key=s.dataset.key;
    if(!updates[sec]) updates[sec]={};
    updates[sec][key]=parseFloat(s.value);
  });
  const msg=document.getElementById('ymsg');
  try{
    const r=await fetch('/api/ytp/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(updates)}).then(r=>r.json());
    msg.innerHTML=`<div class="msg ok">${esc(r.message||'Saved.')}</div>`;
  }catch(e){ msg.innerHTML=`<div class="msg err">${esc(String(e))}</div>`; }
}
async function resetPhysics(){
  const def={};
  L1.forEach(([s,k,l,lo,hi,dv])=>{ if(!def[s]) def[s]={}; def[s][k]=dv; });
  L1_SOURCES.forEach(([s,k,l,lo,hi,dv])=>{ if(!def[s]) def[s]={}; def[s][k]=dv; });
  const msg=document.getElementById('ymsg');
  try{
    await fetch('/api/ytp/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(def)});
    msg.innerHTML='<div class="msg ok">Reset to defaults.</div>';
    setTimeout(loadYtp,300);
  }catch(e){ msg.innerHTML=`<div class="msg err">${esc(String(e))}</div>`; }
}

// ── EXPLAIN SYSTEM ────────────────────────────────────────────────────────────
const EXPLAIN = {
  coherence:{
    calc:'mean(cos_similarity(concept_i, concept_j)) over top-50 active concept pairs',
    body:'How well the current active concepts fit together as a unified thought. High coherence = the engine is focused on a related cluster of ideas. Low coherence = scattered, many unrelated topics active simultaneously. Computed as the mean pairwise cosine similarity of the top-50 MiniLM embeddings in the active concept pool.'
  },
  ambiguity:{
    calc:'0.6 × exploration_trait + 0.4 × (1 − stability_trait)',
    body:'How uncertain or multi-interpretable the current knowledge state is. Drives the engine to seek clarification. Derived from the identity traits: a highly exploratory, low-stability personality produces high ambiguity pressure, pushing the engine toward novel content and contradiction resolution.'
  },
  volatility:{
    calc:'std(activation_deltas) over last 20 concept updates',
    body:'How rapidly concept activations are changing. High volatility = the engine is in a turbulent learning phase, encountering many new concepts that shift the field quickly. Low volatility = stable, consolidating. The engine uses volatility to switch cognitive modes (e.g., reflective when volatile slows).'
  },
  pressure:{
    calc:'mean(tension_score) over active contradiction pairs',
    body:'The total cross-cutting ambiguity pressure from unresolved contradictions in the knowledge graph. When two concepts are asserted with conflicting relationships, tension accumulates. High pressure triggers the engine to prioritize contradiction resolution as its active goal.'
  },
  aiq:{
    calc:'concepts_added_per_minute, smoothed over last 10 cycles',
    body:'AI-Q: the rate at which the engine is acquiring verified knowledge. Measured in concepts per minute (cpm). Green = fast learning (>20 cpm). Amber = moderate (5-20 cpm). Dim = slow or paused. This is the primary health signal — a high AIQ means the pipeline is running efficiently and finding rich content.'
  },
  mode:{
    calc:'StabilityMonitor: Shannon entropy of activation distribution → threshold ladder',
    body:'The current cognitive mode, determined by the Shannon entropy of the JAM field activation distribution over 100 concept samples. Five modes: FOCUSED (low entropy, tight cluster), EXPLORATORY (high entropy, broad search), ASSOCIATIVE (medium, linking across topics), SATURATED (too many competing activations), REFLECTIVE (self-model updating). The mode governs which goal the engine prioritizes.'
  },
  goal:{
    calc:'GoalEngine: 5 drives compete via softmax(score × weight), winner takes action',
    body:'The engine\'s current intrinsic drive. Five competing drives: build_coherence (connect related concepts), reduce_uncertainty (resolve ambiguity), increase_novelty (find new topics), resolve_contradiction (fix conflicts), maintain_stability (consolidate existing knowledge). Each has a score that updates based on the current field state; the highest-scoring drive wins and influences topic selection.'
  },
  energy:{
    calc:'EnergyBudget: pool depletes each operation, regenerates at base_rate per cycle',
    body:'A finite resource pool that governs how much the engine can do per cycle. Each graph write, embedding computation, and LLM call costs energy. The pool regenerates passively. When energy is low, the engine reduces batch sizes and skips expensive operations. This prevents runaway resource consumption during burst learning.'
  },
  nodes:{
    calc:'SELECT COUNT(*) FROM nodes in graph.db (SQLite)',
    body:'The total number of unique concepts (nodes) in the semantic knowledge graph stored in graph.db. Each node is a concept string mapped to a 384-dimensional MiniLM embedding. Edges between nodes carry co-occurrence weights — the more often two concepts appear together, the stronger their edge.'
  },
  entropy:{
    calc:'−Σ p_i log p_i over softmax(top-100 activation scores)',
    body:'Shannon entropy of the JAM field activation distribution. High entropy = many concepts competing equally (broad exploration). Low entropy = one dominant concept cluster (focused mode). The MetaRegulator reads entropy every 60s to tune gain_rate, decay_rate, and diffusion_strength autonomously.'
  },
  pressure_field:{
    calc:'mean(tension_score) injected into JAM field from TensionTracker',
    body:'The aggregate semantic pressure across all active contradictions, projected into the JAM field. Concepts involved in unresolved contradictions receive boosted activation, making the engine more likely to fetch content that could resolve the conflict.'
  },
  mean_activation:{
    calc:'mean(activation_value) over all non-zero nodes in JAM field',
    body:'Average concept activation across the entire JAM field. A low mean with high peak nodes means the engine is focused. A high flat mean means many concepts are equally excited — often a sign of broad unfocused reading.'
  },
  mean_novelty:{
    calc:'mean(1 / (1 + exposure_count)) over active concepts',
    body:'Average novelty score of active concepts. Exposure count tracks how many times each concept has appeared in the feed. Low exposure = high novelty. The engine uses this to bias topic selection toward less-seen territory.'
  },
  mean_tension:{
    calc:'mean(tension_score) per concept from TensionTracker',
    body:'Average cross-cutting tension of active concepts. Tension accumulates when a concept appears in contradictory contexts. High mean tension means the active concepts are contested — the engine\'s knowledge is conflicted and seeking resolution.'
  },
  mean_stability:{
    calc:'mean(stability_score) per concept from StabilityMonitor',
    body:'Average stability of active concepts. Stability = how consistently a concept activates across cycles (not spiking and dying). High stability = consolidated long-term knowledge. Low stability = transient, recently-encountered concepts.'
  },
  gain_rate:{
    calc:'Layer 2 · MetaRegulator adjusts every 60s: if entropy < 0.3: gain += 0.005',
    body:'How quickly JAM field activations grow when a concept is encountered. Controlled autonomously by the MetaRegulator. If the field is too focused (low entropy), gain increases to spread activation. If too diffuse (high entropy), gain decreases to sharpen focus. Layer 2 parameter — never manually set.'
  },
  decay_rate:{
    calc:'Layer 2 · MetaRegulator: activation *= (1 − decay_rate) each tick',
    body:'How quickly inactive concepts fade from the JAM field. Higher decay = faster forgetting = only very recent/frequent concepts stay active. The MetaRegulator increases decay when the field is saturated, to make room for new concepts. Decreases decay during low-novelty periods to retain rare knowledge.'
  },
  diffusion:{
    calc:'Layer 2 · MetaRegulator: activation spreads to graph neighbors at diffusion_strength rate',
    body:'How much activation spreads from active concepts to their neighbors in the knowledge graph. High diffusion = the engine "primes" related concepts before encountering them. Low diffusion = tight, contained activation. The MetaRegulator uses this to control the breadth of pre-activation.'
  },
};

function explain(key, currentVal){
  const info = EXPLAIN[key];
  if(!info) return;
  document.getElementById('ex-title').textContent = key.replace(/_/g,' ').toUpperCase();
  document.getElementById('ex-val').textContent = currentVal !== undefined ? currentVal : '';
  document.getElementById('ex-calc').textContent = 'formula: ' + info.calc;
  document.getElementById('ex-body').textContent = info.body;
  document.getElementById('explain-overlay').classList.add('open');
}
function closeExplain(){
  document.getElementById('explain-overlay').classList.remove('open');
}
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeExplain(); });

// Patch renderCore to make diag values clickable
const _origRenderCore = renderCore;
renderCore = function(d){
  _origRenderCore(d);
  // wrap diag values with clickable spans
  const patches = [
    ['d-coh',   'coherence',  d.coherence],
    ['d-amb',   'ambiguity',  d.ambiguity],
    ['d-vol',   'volatility', d.volatility],
    ['d-pres',  'pressure',   d.pressure],
    ['d-aiq',   'aiq',        (d.aiq_cpm||0).toFixed(1)+' cpm'],
    ['sb-mode', 'mode',       d.mode],
    ['sb-goal', 'goal',       d.goal],
    ['sb-energy','energy',    d.energy],
    ['sb-nodes', 'nodes',     d.nodes],
    ['sb-coh',  'coherence',  d.coherence],
  ];
  patches.forEach(([id,key,val])=>{
    const el=document.getElementById(id);
    if(el && !el.dataset.xwired){
      el.style.cursor='pointer';
      el.title='click for explanation';
      el.dataset.xwired='1';
      el.addEventListener('click',()=>explain(key, val));
    }
  });
};
// Also patch field labels when rendered
const _origLoadField = loadField;
loadField = async function(){
  await _origLoadField();
  [['field-banner','mode'],['field-metrics','mean_activation']].forEach(([id])=>{
    const el=document.getElementById(id);
    if(el) el.querySelectorAll('.met-v').forEach(mv=>{
      const lbl=mv.closest('.met')?.querySelector('.met-l')?.textContent?.trim().toLowerCase().replace(/ /g,'_');
      if(lbl && EXPLAIN[lbl] && !mv.dataset.xwired){
        mv.classList.add('xable'); mv.dataset.xwired='1';
        const v=mv.textContent;
        mv.addEventListener('click',()=>explain(lbl,v));
      }
    });
  });
};
</script>
</body>
</html>"""






LEARNER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Learner Control</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;min-height:100%;background:#07070a;color:#b0b0d8;font-family:Consolas,'Courier New',monospace;font-size:12px}
body{padding:16px 20px 40px;display:grid;grid-template-columns:1fr 1fr;gap:14px}
h1{font-size:10px;letter-spacing:0.22em;text-transform:uppercase;color:#505078;border-bottom:1px solid #1a1a28;padding-bottom:7px;margin-bottom:14px;font-weight:400;grid-column:1/-1}
.card{background:#09090f;border:1px solid #1a1a28;border-radius:2px;padding:12px 14px;margin-bottom:0}
.card-title{font-size:8px;letter-spacing:0.2em;text-transform:uppercase;color:#404060;margin-bottom:10px}
.row{display:flex;align-items:center;gap:8px;margin-bottom:7px}
.row label{min-width:150px;font-size:9px;color:#606080;letter-spacing:0.06em}
.row input[type=range]{flex:1;accent-color:#4a9eff;height:3px;cursor:pointer}
.row .val{min-width:60px;text-align:right;font-size:10px;color:#9898c8}
.row input[type=number]{width:80px;background:#0d0d18;border:1px solid #1a1a28;color:#9898c8;font:10px Consolas;padding:2px 6px;border-radius:2px;text-align:right}
.row input[type=checkbox]{accent-color:#4a9eff;width:14px;height:14px;cursor:pointer}
.row .clbl{font-size:9px;color:#606080;letter-spacing:0.06em}
.btn{display:inline-block;padding:5px 16px;border:1px solid #1a1a28;background:#0a0a18;color:#606080;font:9px Consolas;cursor:pointer;border-radius:2px;letter-spacing:0.12em;text-transform:uppercase;margin-right:6px;margin-top:4px}
.btn:hover{border-color:#4a9eff;color:#4a9eff}
.btn.danger:hover{border-color:#ff4444;color:#ff4444}
.btn.green{border-color:#44ff88;color:#44ff88}
.sep{border:none;border-top:1px solid #111120;margin:10px 0}
/* source grid */
.src-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px}
.src-row{display:flex;align-items:center;gap:6px;padding:3px 0}
.src-name{min-width:120px;font-size:9px;color:#505078;letter-spacing:0.06em}
.src-w{width:50px;background:#0d0d18;border:1px solid #1a1a28;color:#9898c8;font:9px Consolas;padding:2px 4px;border-radius:2px;text-align:center}
/* live stats */
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
.stat{background:#070710;border:1px solid #111120;border-radius:2px;padding:8px 10px}
.stat-key{font-size:7px;letter-spacing:0.16em;text-transform:uppercase;color:#303050;margin-bottom:4px}
.stat-val{font-size:16px;color:#b0b0d8}
/* activity feed */
.feed{max-height:180px;overflow-y:auto;border-top:1px solid #111120;margin-top:8px}
.feed-row{display:flex;gap:6px;padding:2px 0;border-bottom:1px solid #0a0a12;font-size:9px;line-height:1.6}
.feed-src{min-width:90px;color:#404060;text-overflow:ellipsis;overflow:hidden;white-space:nowrap}
.feed-title{flex:1;color:#606080;text-overflow:ellipsis;overflow:hidden;white-space:nowrap}
.feed-ok{color:#44ff88;min-width:20px}
.msg{font-size:9px;padding:5px 0;color:#44ff88;letter-spacing:0.08em;min-height:18px}
/* full-width bottom */
.full{grid-column:1/-1}
</style>
</head>
<body>
<h1>Learner Control Panel</h1>

<!-- LEFT: Live stats + feed -->
<div>
  <div class="card">
    <div class="card-title">Live Stats</div>
    <div class="stat-grid">
      <div class="stat"><div class="stat-key">Concepts</div><div class="stat-val" id="st-concepts">—</div></div>
      <div class="stat"><div class="stat-key">Sentences</div><div class="stat-val" id="st-sent">—</div></div>
      <div class="stat"><div class="stat-key">Active</div><div class="stat-val" id="st-active">—</div></div>
      <div class="stat"><div class="stat-key">Mode</div><div class="stat-val" style="font-size:12px" id="st-mode">—</div></div>
      <div class="stat"><div class="stat-key">Entropy</div><div class="stat-val" id="st-ent">—</div></div>
      <div class="stat"><div class="stat-key">Coherence</div><div class="stat-val" id="st-coh">—</div></div>
    </div>
    <div style="font-size:9px;color:#303050;margin-bottom:6px">Now reading:</div>
    <div id="st-reading" style="font-size:9px;color:#4a6aaa;letter-spacing:0.04em;margin-bottom:8px;min-height:14px">—</div>
    <div class="card-title" style="margin-top:6px">Event Stream</div>
    <div class="feed" id="feed"></div>
  </div>
</div>

<!-- RIGHT: Speed + source controls -->
<div>
  <div class="card">
    <div class="card-title">Learning Speed</div>
    <div class="row"><label>Topics per cycle</label><input type="range" id="s-topics" min="10" max="500" step="10" value="200"><span class="val" id="v-topics">200</span></div>
    <div class="row"><label>Workers (search)</label><input type="range" id="s-wsearch" min="8" max="512" step="8" value="256"><span class="val" id="v-wsearch">256</span></div>
    <div class="row"><label>Workers (fetch)</label><input type="range" id="s-wfetch" min="8" max="512" step="8" value="256"><span class="val" id="v-wfetch">256</span></div>
    <div class="row"><label>Max results/source</label><input type="range" id="s-maxres" min="3" max="30" step="1" value="15"><span class="val" id="v-maxres">15</span></div>
    <div class="row"><label>Max sentences/article</label><input type="range" id="s-maxsent" min="5" max="50" step="5" value="25"><span class="val" id="v-maxsent">25</span></div>
    <div class="row"><label>Fetch timeout (s)</label><input type="range" id="s-ftimeout" min="3" max="20" step="1" value="8"><span class="val" id="v-ftimeout">8</span></div>
    <div class="row"><label>Encoder workers</label><input type="range" id="s-enc" min="1" max="8" step="1" value="4"><span class="val" id="v-enc">4</span></div>
    <div class="sep"></div>
    <div class="row"><label>Cycle idle sleep (s)</label><input type="range" id="s-cycle" min="0" max="30" step="1" value="0"><span class="val" id="v-cycle">0</span></div>
    <div class="row"><input type="checkbox" id="cb-paused"><span class="clbl" style="margin-left:4px">Pause learning</span></div>
    <div class="msg" id="msg-speed"></div>
    <button class="btn green" onclick="applySpeed()">Apply Speed Settings</button>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="card-title">JAM Field Physics</div>
    <div class="row"><label>Gain rate</label><input type="range" id="s-gain" min="0.01" max="0.50" step="0.01" value="0.06"><span class="val" id="v-gain">0.06</span></div>
    <div class="row"><label>Decay rate</label><input type="range" id="s-decay" min="0.990" max="0.9999" step="0.0001" value="0.9972"><span class="val" id="v-decay">0.9972</span></div>
    <div class="row"><label>Diffusion strength</label><input type="range" id="s-diff" min="0.01" max="0.30" step="0.01" value="0.12"><span class="val" id="v-diff">0.12</span></div>
    <div class="msg" id="msg-field"></div>
    <button class="btn green" onclick="applyField()">Apply Field Settings</button>
  </div>
</div>

<!-- Full-width: source weights -->
<div class="card full">
  <div class="card-title">Source Weights &nbsp;<span style="color:#252545;font-size:8px">(0 = disabled)</span></div>
  <div class="src-grid" id="src-grid"></div>
  <div class="msg" id="msg-src"></div>
  <button class="btn green" onclick="applySources()" style="margin-top:8px">Apply Source Weights</button>
  <button class="btn" onclick="equalSources()">Equalize All</button>
  <button class="btn" onclick="resetSources()">Reset Defaults</button>
</div>

<script>
// ── Slider sync ───────────────────────────────────────────────────────────────
function bindSlider(id, valId, fmt){
  const sl=document.getElementById(id),vl=document.getElementById(valId);
  if(!sl||!vl)return;
  const upd=()=>vl.textContent=fmt?fmt(+sl.value):(+sl.value);
  sl.addEventListener('input',upd);upd();
}
bindSlider('s-topics','v-topics');bindSlider('s-wsearch','v-wsearch');
bindSlider('s-wfetch','v-wfetch');bindSlider('s-maxres','v-maxres');
bindSlider('s-maxsent','v-maxsent');bindSlider('s-ftimeout','v-ftimeout');
bindSlider('s-enc','v-enc');bindSlider('s-cycle','v-cycle');
bindSlider('s-gain','v-gain',v=>(+v).toFixed(2));
bindSlider('s-decay','v-decay',v=>(+v).toFixed(4));
bindSlider('s-diff','v-diff',v=>(+v).toFixed(2));

// ── Source weights grid ───────────────────────────────────────────────────────
const SOURCES_DEFAULT={
  wikipedia:2.5,simplewiki:1.0,arxiv:2.0,openalex:2.0,semanticscholar:2.0,
  pubmed:1.8,crossref:1.5,reddit:1.8,hackernews:1.8,stackexchange:1.5,
  devto:1.2,gutenberg:1.0,newsrss:1.5,internetarchive:0.8,web:2.5
};
const SOURCES_ORDER=Object.keys(SOURCES_DEFAULT);

function buildSourceGrid(vals){
  const g=document.getElementById('src-grid');
  g.innerHTML=SOURCES_ORDER.map(k=>`
    <div class="src-row">
      <span class="src-name">${k}</span>
      <input class="src-w" type="number" id="sw-${k}" min="0" max="10" step="0.1"
             value="${(vals||SOURCES_DEFAULT)[k]??0}">
    </div>`).join('');
}
buildSourceGrid(SOURCES_DEFAULT);

function getSrcVals(){
  const v={};
  SOURCES_ORDER.forEach(k=>{const e=document.getElementById('sw-'+k);if(e)v[k]=parseFloat(e.value)||0;});
  return v;
}
function equalSources(){
  SOURCES_ORDER.forEach(k=>{const e=document.getElementById('sw-'+k);if(e)e.value=1.5;});
}
function resetSources(){
  buildSourceGrid(SOURCES_DEFAULT);
}

// ── API calls ─────────────────────────────────────────────────────────────────
function flash(id,msg,ok=true){
  const e=document.getElementById(id);
  if(e){e.textContent=msg;e.style.color=ok?'#44ff88':'#ff4444';setTimeout(()=>e.textContent='',3000);}
}

async function applySpeed(){
  const body={
    topics_per_cycle:+document.getElementById('s-topics').value,
    n_workers_search:+document.getElementById('s-wsearch').value,
    n_workers_fetch: +document.getElementById('s-wfetch').value,
    max_results_per: +document.getElementById('s-maxres').value,
    max_sentences:   +document.getElementById('s-maxsent').value,
    fetch_timeout:   +document.getElementById('s-ftimeout').value,
    n_encoder_workers:+document.getElementById('s-enc').value,
    cycle_time:      +document.getElementById('s-cycle').value,
    paused:          document.getElementById('cb-paused').checked?'1':'0',
  };
  try{
    const r=await fetch('/api/learner/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(r.ok)flash('msg-speed','Applied.');
    else flash('msg-speed','Error: '+r.status,false);
  }catch(e){flash('msg-speed','Network error',false);}
}

async function applyField(){
  const body={
    gain_rate:  +document.getElementById('s-gain').value,
    decay_rate: +document.getElementById('s-decay').value,
    diffusion_strength:+document.getElementById('s-diff').value,
  };
  try{
    const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(r.ok)flash('msg-field','Applied.');
    else flash('msg-field','Error: '+r.status,false);
  }catch(e){flash('msg-field','Network error',false);}
}

async function applySources(){
  const weights=getSrcVals();
  try{
    const r=await fetch('/api/learner/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({weights})});
    if(r.ok)flash('msg-src','Source weights saved.');
    else flash('msg-src','Error: '+r.status,false);
  }catch(e){flash('msg-src','Network error',false);}
}

// ── Live stats polling ────────────────────────────────────────────────────────
const MC={focused:'#f59e0b',exploratory:'#3b82f6',associative:'#8b5cf6',
          conflicted:'#ef4444',saturated:'#8b5cf6',drifting:'#10b981'};

async function pollStats(){
  try{
    const d=await fetch('/api/state').then(r=>r.json());
    const sv=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v??'—';};
    sv('st-concepts',(d.node_count||0).toLocaleString());
    sv('st-sent',(d.total_sentences||0).toLocaleString());
    sv('st-active',(d.active_count||0).toLocaleString());
    sv('st-ent',(d.entropy||0).toFixed(4));
    sv('st-coh',(d.coherence||0).toFixed(4));
    const me=document.getElementById('st-mode');
    if(me){me.textContent=d.mode||'—';me.style.color=MC[d.mode]||'#b0b0d8';}
    if(d.reading){sv('st-reading',d.reading.slice(0,70));}
  }catch(e){}
  try{
    const f=await fetch('/api/all').then(r=>r.json());
    const feed=document.getElementById('feed');
    if(feed&&f.feed){
      const rows=(f.feed||[]).slice(-30).reverse();
      feed.innerHTML=rows.map(e=>`
        <div class="feed-row">
          <span class="feed-src">${e.source||'—'}</span>
          <span class="feed-title">${(e.title||'').slice(0,55)}</span>
          <span class="feed-ok" style="color:${e.status==='ok'?'#44ff88':'#555'}">${e.status==='ok'?e.concepts||0:'✗'}</span>
        </div>`).join('');
    }
  }catch(e){}
}

pollStats();setInterval(pollStats,2000);

// Pause checkbox sync
async function syncPaused(){
  try{
    const d=await fetch('/api/state').then(r=>r.json());
    document.getElementById('cb-paused').checked=d.paused===true||d.paused==='1';
  }catch(e){}
}
syncPaused();
</script>
</body>
</html>"""


GOALS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Goals</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;min-height:100%;background:#070709;color:#b0b0d8;font-family:Consolas,'Courier New',monospace;font-size:12px}
body{padding:18px 22px 32px}
h1{font-size:11px;letter-spacing:0.22em;text-transform:uppercase;color:#505078;border-bottom:1px solid #222238;padding-bottom:8px;margin-bottom:18px;font-weight:400}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;margin-bottom:18px}
.card{background:#09090f;border:1px solid #1a1a28;border-radius:2px;padding:12px 14px}
.card-key{font-size:8px;letter-spacing:0.18em;text-transform:uppercase;color:#404060;margin-bottom:6px}
.card-val{font-size:18px;color:#b0b0d8;line-height:1.2}
.card-sub{font-size:9px;color:#353555;margin-top:4px}
.section{font-size:8px;letter-spacing:0.2em;text-transform:uppercase;color:#303050;border-bottom:1px solid #111120;padding-bottom:4px;margin:18px 0 10px}
.bar-row{display:flex;align-items:center;gap:10px;padding:3px 0}
.bar-lbl{min-width:140px;color:#606080;font-size:10px;letter-spacing:0.05em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{flex:1;height:5px;background:#0d0d18;border-radius:2px;overflow:hidden}
.bar-fill{height:100%;border-radius:2px;transition:width 1s}
.bar-val{min-width:52px;text-align:right;color:#404060;font-size:9px}
.pill{display:inline-block;padding:2px 10px;border-radius:10px;font-size:9px;letter-spacing:0.1em;text-transform:uppercase;margin-right:4px;margin-bottom:4px}
.goal-row{display:flex;align-items:baseline;gap:8px;padding:5px 0;border-bottom:1px solid #0d0d1a}
.goal-name{min-width:160px;color:#7878a8;font-size:10px}
.goal-score{color:#b0b0d8;font-size:13px}
.goal-desc{color:#303050;font-size:9px;margin-left:auto}
</style>
</head>
<body>
<h1>Cognitive Goals &amp; Drives</h1>

<div class="grid" id="metric-cards">
  <div class="card"><div class="card-key">Active Goal</div><div class="card-val" id="g-goal">—</div><div class="card-sub" id="g-mode">mode: —</div></div>
  <div class="card"><div class="card-key">Entropy</div><div class="card-val" id="g-ent">—</div><div class="card-sub" id="g-pres">pressure: —</div></div>
  <div class="card"><div class="card-key">Coherence</div><div class="card-val" id="g-coh">—</div><div class="card-sub" id="g-ten">tension: —</div></div>
  <div class="card"><div class="card-key">Gain / Decay</div><div class="card-val" id="g-gain">—</div><div class="card-sub" id="g-dec">—</div></div>
</div>

<div class="section">Goal Priority (current election)</div>
<div id="goal-ladder"></div>

<div class="section">Field Scalars</div>
<div id="scalar-bars"></div>

<div class="section">Surviving Goals (worldview)</div>
<div id="worldview-goals"></div>

<div class="section">Causal World Model — top edges</div>
<div id="world-model"></div>

<script>
const GOALS=[
  {id:'resolve_tension',   label:'Resolve Tension',   desc:'reduce cross-cluster conflict',  color:'#ef4444'},
  {id:'expand_knowledge',  label:'Expand Knowledge',  desc:'grow the concept graph',          color:'#4a9eff'},
  {id:'consolidate',       label:'Consolidate',        desc:'strengthen existing connections', color:'#44ff88'},
  {id:'stabilise',         label:'Stabilise',          desc:'reduce entropy, find order',      color:'#f59e0b'},
  {id:'explore',           label:'Explore',            desc:'associative drift, serendipity',  color:'#8b5cf6'},
];

const SCALARS=[
  {id:'gain_rate',          label:'Gain Rate',          lo:0.10,hi:0.50, color:'#4a9eff'},
  {id:'decay_rate',         label:'Decay Rate',         lo:0.10,hi:0.999,color:'#44ff88'},
  {id:'diffusion_strength', label:'Diffusion Strength', lo:0.05,hi:0.25, color:'#8b5cf6'},
  {id:'entropy',            label:'Entropy',            lo:0,hi:1,       color:'#f59e0b'},
  {id:'pressure',           label:'Pressure',           lo:0,hi:1,       color:'#ef4444'},
];

function pct(v,lo,hi){return Math.round(Math.max(0,Math.min(100,(v-lo)/(hi-lo)*100)));}
function sv(id,v){const e=document.getElementById(id);if(e)e.textContent=v??'—';}

async function poll(){
  try{
    const d=await fetch('/api/state').then(r=>r.json());
    const reg=d.reg||d;
    const goal=reg.goal||d.goal||'—';
    const mode=reg.mode||d.mode||'—';
    const ent=+(reg.entropy??d.entropy??0),pres=+(reg.pressure??0);
    const coh=+(d.coherence??0),ten=+(d.mean_tension??0);
    const gain=+(reg.gain_rate??0.3),dec=+(reg.decay_rate??0.997);
    const diff=+(reg.diffusion_strength??0.12);

    sv('g-goal', goal.replace(/_/g,' '));
    sv('g-mode', 'mode: '+mode);
    sv('g-ent',  ent.toFixed(4));
    sv('g-pres', 'pressure: '+pres.toFixed(4));
    sv('g-coh',  coh.toFixed(4));
    sv('g-ten',  'tension: '+ten.toFixed(4));
    sv('g-gain', gain.toFixed(4));
    sv('g-dec',  'decay: '+dec.toFixed(5)+' · diff: '+diff.toFixed(4));

    // Goal ladder — score each goal by how strongly its condition is met
    const scores={
      resolve_tension:  Math.min(1, ten/0.5),
      expand_knowledge: Math.max(0, 1-coh/0.1),
      consolidate:      coh>0.45&&d.mean_novelty<0.3 ? 0.9 : 0.1,
      stabilise:        Math.min(1, ent/0.7),
      explore:          0.4,
    };
    scores[goal.replace(/ /g,'_')]=Math.max(scores[goal.replace(/ /g,'_')]||0, 0.9);
    const ladder=document.getElementById('goal-ladder');
    if(ladder){
      ladder.innerHTML=GOALS.sort((a,b)=>(scores[b.id]||0)-(scores[a.id]||0)).map(g=>{
        const s=scores[g.id]||0;
        const active=g.id===goal||g.id===goal.replace(/ /g,'_');
        return`<div class="goal-row" style="${active?'border-left:2px solid '+g.color+';padding-left:8px':''}">
          <span class="goal-name" style="${active?'color:'+g.color:''}">
            ${active?'▶ ':''}<b>${g.label}</b></span>
          <span class="goal-score" style="color:${g.color}">${(s*100).toFixed(0)}%</span>
          <span style="flex:1;margin:0 10px;height:3px;background:#0d0d18;border-radius:2px;display:inline-block;vertical-align:middle">
            <span style="display:block;height:100%;width:${(s*100).toFixed(0)}%;background:${g.color};border-radius:2px"></span>
          </span>
          <span class="goal-desc">${g.desc}</span></div>`;
      }).join('');
    }

    // Scalar bars
    const vals={gain_rate:gain,decay_rate:dec,diffusion_strength:diff,entropy:ent,pressure:pres};
    const sb=document.getElementById('scalar-bars');
    if(sb) sb.innerHTML=SCALARS.map(s=>{
      const v=vals[s.id]??0,p=pct(v,s.lo,s.hi);
      return`<div class="bar-row">
        <span class="bar-lbl">${s.label}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${p}%;background:${s.color}"></span></span>
        <span class="bar-val">${v.toFixed(5)}</span></div>`;
    }).join('');
  }catch(e){}

  // Worldview surviving goals
  try{
    const wv=await fetch('/api/cognition').then(r=>r.json()).then(d=>d.worldview||{});
    const sg=wv.surviving_goals||[];
    const wg=document.getElementById('worldview-goals');
    if(wg&&sg.length) wg.innerHTML=sg.map(g=>
      `<div class="bar-row"><span class="bar-lbl">${(g.goal||'—').replace(/_/g,' ')}</span>
       <span class="bar-track"><span class="bar-fill" style="width:${Math.round((g.stability_score||0)*100)}%;background:#44ff88"></span></span>
       <span class="bar-val">${(g.stability_score||0).toFixed(3)}</span></div>`
    ).join('');
    else if(wg&&!sg.length) wg.innerHTML='<div style="color:#252540;font-size:10px;padding:6px">worldview builds after 3h+ runtime</div>';
  }catch(e){}

  // World model causal edges
  try{
    const wm=await fetch('/api/cognition').then(r=>r.json()).then(d=>d.world_model||{});
    const edges=Object.entries(wm.edges||{}).slice(0,14);
    const wme=document.getElementById('world-model');
    if(wme) wme.innerHTML=edges.length?edges.map(([k,v])=>{
      const [from,to]=k.split('→').map(s=>s.trim());
      const w=+(v.weight||v||0);
      return`<div class="bar-row">
        <span class="bar-lbl" style="min-width:200px"><span style="color:#4a9eff">${(from||'').slice(0,20)}</span> → <span style="color:#44ff88">${(to||'').slice(0,20)}</span></span>
        <span class="bar-track"><span class="bar-fill" style="width:${Math.round(Math.min(1,w)*100)}%;background:#8b5cf6"></span></span>
        <span class="bar-val">${w.toFixed(3)}</span></div>`;
    }).join(''):'<div style="color:#252540;font-size:10px;padding:6px">no causal edges yet</div>';
  }catch(e){}
}

poll();setInterval(poll,3000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def handle_error(self, *a): pass   # never let a request crash the server

    def do_GET(self):
      try:
        self._do_GET_inner()
      except Exception as _e:
        try: self._send(500, 'text/plain', str(_e).encode())
        except Exception: pass

    def _do_GET_inner(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        if path in ('/', '/index.html'):
            self._send(200, 'text/html; charset=utf-8', HTML.encode())
        elif path == '/logo.svg':
            p = Path(__file__).parent / 'assets' / 'logo.svg'
            self._send(200, 'image/svg+xml', p.read_bytes() if p.exists() else b'')
        elif path.startswith('/fonts/'):
            fname = path.split('/')[-1]
            p = Path(__file__).parent / 'assets' / 'fonts' / fname
            if p.exists() and p.suffix == '.woff2':
                self._send(200, 'font/woff2', p.read_bytes())
            else:
                self._send(404, 'text/plain', b'not found')
        elif path == '/voice':
            self._send(200, 'text/html; charset=utf-8', VOICE_HTML.encode())
        elif path == '/live':
            self._send(200, 'text/html; charset=utf-8', LIVE_HTML.encode())
        elif path == '/goals':
            self._send(200, 'text/html; charset=utf-8', GOALS_HTML.encode())
        elif path == '/learner':
            self._send(200, 'text/html; charset=utf-8', LEARNER_HTML.encode())
        elif path == '/health':
            self._send(200, 'text/plain', b'ok')
        elif path == '/api/state':
            self._json(api_state())
        elif path == '/api/field':
            self._json(api_field())
        elif path == '/api/cognition':
            self._json(api_cognition())
        elif path == '/api/umber':
            self._json(api_umber())
        elif path == '/api/intent':
            self._json(api_intent())
        elif path == '/api/feedback':
            self._json(api_feedback())
        elif path == '/api/echo':
            self._json(api_echo())
        elif path == '/api/graph3d':
            n = int(qs.get('n', [500])[0])
            self._json(api_graph3d(max_nodes=n))
        elif path == '/api/config':
            d = api_config()
            d['is_local'] = self.client_address[0] in ('127.0.0.1', '::1', 'localhost')
            self._json(d)
        elif path == '/api/runner/status':
            job_id = qs.get('job_id', [None])[0]
            with _job_lock:
                job = dict(_jobs.get(job_id, {'status': 'unknown', 'result': {}}))
            self._json(job)
        else:
            self._send(404, 'text/plain', b'not found')

    def do_POST(self):
      try:
        self._do_POST_inner()
      except Exception as _e:
        try: self._send(500, 'text/plain', str(_e).encode())
        except Exception: pass

    def _do_POST_inner(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try:    data = json.loads(body) if body else {}
        except Exception: data = {}
        path = self.path

        if path == '/api/set_speed':
            # Knob speed control — writes learning rate params to engine_config.json
            try:
                cfg = _jload('engine_config.json', {})
                lcfg = cfg.get('learning', {})
                for k in ('cycle_time','n_workers','topics_per_cycle'):
                    if k in data:
                        lcfg[k] = data[k]
                cfg['learning'] = lcfg
                tmp = DATA / 'engine_config.json.tmp'
                tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
                tmp.replace(DATA / 'engine_config.json')
                self._json({'ok': True})
            except Exception as e:
                self._json({'ok': False, 'error': str(e)})
            return

        if path == '/api/learner/config':
            # Write learner speed params to engine_config.json, also handle pause
            try:
                cfg = _jload('engine_config.json', {})
                for key in ('topics_per_cycle','n_workers_search','n_workers_fetch',
                            'max_results_per','max_sentences','fetch_timeout',
                            'n_encoder_workers','cycle_time'):
                    if key in data:
                        cfg[key] = data[key]
                tmp = DATA / 'engine_config.json.tmp'
                tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
                tmp.replace(DATA / 'engine_config.json')
                if 'paused' in data:
                    (DATA / 'paused.txt').write_text(str(data['paused']), encoding='utf-8')
                self._json({'ok': True})
            except Exception as e:
                self._json({'ok': False, 'error': str(e)})
            return

        if path == '/api/learner/sources':
            # Persist custom source weights to engine_config.json
            try:
                weights = data.get('weights', {})
                cfg = _jload('engine_config.json', {})
                cfg['source_weights'] = weights
                tmp = DATA / 'engine_config.json.tmp'
                tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
                tmp.replace(DATA / 'engine_config.json')
                self._json({'ok': True})
            except Exception as e:
                self._json({'ok': False, 'error': str(e)})
            return

        if path == '/api/goal/compile':
            self._json(api_goal_compile(data.get('goal', '')))
            return

        if path == '/api/goal/feedback':
            goal       = data.get('goal', '')
            confidence = float(data.get('confidence', 0.7))
            try:
                from learner import AutoLearner
                al = AutoLearner.get()
                if al._umber and goal:
                    slug = re.sub(r'[^a-z0-9_]', '_', goal[:40].lower().strip())
                    al._umber.inject(slug, min(1.0, confidence))
                self._json({'ok': True})
            except Exception as e:
                self._json({'ok': False, 'error': str(e)})
            return

        if path == '/api/image/embed':
            self._json(api_embed_image(data.get('data',''), data.get('label','image')))
            return

        if path == '/api/runner/stream':
            prompt = data.get('prompt', '')
            model  = data.get('model', '') or 'qwen2.5:1.5b'
            self._stream_pipeline(prompt, model)
            return

        if path == '/api/runner/run':
            prompt  = data.get('prompt', '')
            model   = data.get('model', 'qwen2.5:1.5b')
            job_id  = str(uuid.uuid4())
            with _job_lock:
                _jobs[job_id] = {'status': 'running', 'result': {}}
            threading.Thread(target=_run_pipeline, args=(job_id, prompt, model), daemon=True).start()
            self._json({'job_id': job_id})

        elif path.startswith('/api/tunnel/'):
            parts  = path.split('/')
            action = parts[3] if len(parts) > 3 else ''
            which  = parts[4] if len(parts) > 4 else 'main'
            pid_f  = DATA / ('viz_tunnel.pid' if which == 'viz' else 'tunnel.pid')
            log_f  = DATA / ('viz_tunnel.log' if which == 'viz' else 'tunnel.log')
            port   = 8502 if which == 'viz' else 8501
            if action == 'start':
                err = _start_tunnel(port, pid_f, log_f)
                self._json({'ok': not err, 'error': err})
            else:
                _stop_tunnel(pid_f)
                self._json({'ok': True})

        elif path.startswith('/api/ego/'):
            action = path.split('/')[-1]
            name   = data.get('name', '')
            if not name:
                self._json({'ok': False, 'message': 'no name'}); return
            cfg_path = ROOT / 'data' / 'engine_config.json'
            try:    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            except Exception: cfg = {}
            def _snap(cfg):
                return {s: dict(cfg.get(s, {})) for s in ('identity','evolver','meta_state','goals','attention')}
            if action == 'save':
                cfg.setdefault('egos', {})[name] = _snap(cfg)
                cfg['active_ego'] = name
                _save_cfg(cfg)
                self._json({'ok': True, 'message': f"Ego '{name}' saved."})
            elif action == 'load' and name in cfg.get('egos', {}):
                for sec, vals in cfg['egos'][name].items():
                    cfg.setdefault(sec, {}).update(vals)
                cfg['active_ego'] = name
                _save_cfg(cfg)
                self._json({'ok': True, 'message': f"Ego '{name}' loaded."})
            elif action == 'delete':
                cfg.get('egos', {}).pop(name, None)
                if cfg.get('active_ego') == name: cfg.pop('active_ego', None)
                _save_cfg(cfg)
                self._json({'ok': True, 'message': f"Ego '{name}' deleted."})
            else:
                self._json({'ok': False, 'message': 'unknown action'})

        elif path == '/api/ytp/save':
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                self._json({'ok': False, 'message': 'local only'}); return
            cfg_path = ROOT / 'data' / 'engine_config.json'
            try:    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            except Exception: cfg = {}
            for sec, vals in data.items():
                if isinstance(vals, dict):
                    cfg.setdefault(sec, {}).update(vals)
            _save_cfg(cfg)
            self._json({'ok': True, 'message': 'Saved.'})

        else:
            self._send(404, 'text/plain', b'not found')

    def _json(self, obj):
        body = json.dumps(obj, default=str).encode()
        self._send(200, 'application/json', body)

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _stream_pipeline(self, prompt: str, model: str):
        """SSE endpoint — streams LLM tokens as they arrive."""
        import requests as _req, time as _t
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        def _sse(event: str, data: str):
            msg = f'event: {event}\ndata: {data}\n\n'
            try:
                self.wfile.write(msg.encode())
                self.wfile.flush()
            except Exception:
                pass

        try:
            t0 = _t.perf_counter()
            cfg, ix, extract, detect_and_log, build_prompt, call_llm = _get_pipeline(model)

            # ── Graph-Grounded RAG retrieval ──────────────────────────────────
            import sys as _sys
            _sys.path.insert(0, str(ROOT / 'src'))
            from graph_rag import GraphRAG
            rag       = GraphRAG.get()
            rag_result = rag.retrieve(prompt, top_k=20, edge_k=6)
            rag_ctx    = rag_result['context_str']
            rag_stats  = rag_result['stats']
            rag_nodes  = rag_result['nodes']
            # ─────────────────────────────────────────────────────────────────

            concepts = extract(prompt)
            result   = detect_and_log(prompt, concepts)
            mod      = build_prompt(concepts, result, index=ix)
            ix.update(concepts); ix.save()
            pre_ms = round((_t.perf_counter() - t0) * 1000)

            # Send metadata before streaming starts
            _sse('meta', json.dumps({
                'concepts':   [c.text for c in concepts],
                'ambiguity':  {'score': result.score, 'level': result.level},
                'pre_ms':     pre_ms,
                'rag_nodes':  [{'text': n['text'], 'sim': n['sim'],
                                'edges': n['edges'][:3]} for n in rag_nodes[:8]],
                'rag_stats':  rag_stats,
            }))

            # Stream tokens from Ollama — system prompt now includes graph context
            full_prompt = (
                f"[SYSTEM]\n{mod.system_prompt}\n\n"
                f"{rag_ctx}\n\n"
                f"[USER]\n{prompt}"
            )
            payload = {
                'model':       model,
                'prompt':      full_prompt,
                'stream':      True,
                'keep_alive':  -1,
                'num_predict': 200,
            }
            import yaml as _yaml
            endpoint = _yaml.safe_load((ROOT / 'config.yaml').read_text())['model']['endpoint']
            response_text = ''
            with _req.post(endpoint, json=payload, stream=True, timeout=120) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get('response', '')
                    if token:
                        response_text += token
                        _sse('token', json.dumps(token))
                    if chunk.get('done'):
                        llm_ms = round((_t.perf_counter() - t0) * 1000) - pre_ms
                        _sse('done', json.dumps({'total_ms': pre_ms + llm_ms, 'llm_ms': llm_ms}))
                        break

            _sse_meta = {
                'concepts_in':  [c.text for c in concepts],
                'ambiguity_in': result.score if result else 0.3,
                'llm_ms':       llm_ms,
                'strategy':     mod.level if mod else 'medium',
            }
            threading.Thread(
                target=_feedback_async,
                args=(response_text, ix, str(uuid.uuid4()), prompt, _sse_meta),
                daemon=True,
            ).start()

        except Exception as e:
            _sse('error', json.dumps({'error': str(e)}))


if __name__ == '__main__':
    try:
        from learner import AutoLearner
        _learner = AutoLearner.get()
        _learner.start()
        print('[AE] AutoLearner started')
    except Exception as _e:
        print(f'[AE] AutoLearner error: {_e}')

    # Pre-load pipeline modules + warm LLM + encoder so first cycle has zero cold-start
    def _warmup():
        try:
            # Warm encoder first — it's the EXTRACT bottleneck on first cycle (5s model load)
            from extractor import _get_encoder
            enc = _get_encoder()
            enc.encode(['warmup'], batch_size=1, show_progress_bar=False,
                       convert_to_numpy=True, normalize_embeddings=False)
            print('[AE] encoder warmed up')
        except Exception as _ee:
            print(f'[AE] encoder warmup skipped: {_ee}')
        try:
            import yaml
            _cfg = yaml.safe_load((ROOT / 'config.yaml').read_text())
            _get_pipeline(_cfg['model']['name'])   # caches all imports
            from modulator import warmup_model
            warmup_model(_cfg)                     # loads model into VRAM
            print('[AE] pipeline + LLM warmed up')
        except Exception as _we:
            print(f'[AE] warmup skipped: {_we}')
    threading.Thread(target=_warmup, daemon=True).start()

    url = f'http://localhost:{PORT}'
    threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True).start()
    class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    server = _ThreadedHTTPServer(('localhost', PORT), Handler)
    print(f'[AE] Server at {url}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
