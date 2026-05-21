"""
Phase 1 smoke test: send a prompt to Ollama, print the response.
Run from the project root with the venv active:
    python src/smoke_test.py
"""
import json
import os
import sys

import requests
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from logger import get_logger

log = get_logger('smoke_test')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)


def call_ollama(prompt: str, cfg: dict) -> str:
    payload = {
        'model': cfg['model']['name'],
        'prompt': prompt,
        'stream': cfg['model']['stream'],
    }
    log.debug('Sending prompt: %s', prompt)
    resp = requests.post(cfg['model']['endpoint'], json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()['response']


def main():
    cfg = load_config()
    log.info('Config loaded. Model: %s', cfg['model']['name'])

    prompt = (
        "In one sentence, explain what semantic ambiguity is "
        "and why it matters for language models."
    )
    log.info('Calling Ollama…')
    response = call_ollama(prompt, cfg)

    log.info('Response received.')
    print('\n--- Ollama response ---')
    print(response.strip())
    print('-----------------------\n')


if __name__ == '__main__':
    main()
