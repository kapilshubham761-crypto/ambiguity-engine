import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from discover import search_sources, SOURCE_LABELS

results = search_sources(
    'consciousness and attention',
    ['wikipedia', 'arxiv', 'gutenberg', 'reddit', 'openalex'],
    max_per_source=2,
)
current_source = None
for r in results:
    src = r['source']
    if src != current_source:
        current_source = src
        print('\n[' + SOURCE_LABELS[current_source] + ']')
    print('  ' + r['title'][:70])
