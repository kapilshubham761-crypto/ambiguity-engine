"""
Node [A] — Curriculum
====================
Pure data. No logic, no imports, no side-effects.
Every other block that needs stage info imports from here.

Contents
--------
CURRICULUM      list[dict]   8 developmental stages with topic lists
STAGE_CONFIG    list[dict]   per-stage source/readability/modifier config
STAGE_TARGETS   dict         expected node + edge counts per stage (for grading)
"""

# ======================================================================== #
# CURRICULUM — 8 developmental stages                                       #
# Each stage: label, description, topics[]                                 #
# ======================================================================== #

CURRICULUM = [
    {
        'label':       'Ages 1–5  (Foundations)',
        'description': 'Concrete, sensory, immediate — the first words a child hears',
        'topics': [
            'animals farm pets',
            'colours shapes sizes',
            'family mother father siblings',
            'food eating fruit vegetables',
            'body parts feelings emotions',
            'weather sun rain wind snow',
            'day night sleep wake',
            'numbers counting simple math',
            'nursery rhymes songs children',
            'fairy tales simple stories',
            'trees flowers plants garden',
            'water river sea beach sand',
            'home house rooms kitchen bedroom',
            'birds insects butterflies nature',
            'toys play imagination pretend',
        ],
    },
    {
        'label':       'Year 1  (Early School)',
        'description': 'First structured learning — letters, community, simple science',
        'topics': [
            'alphabet reading letters words',
            'community helpers doctor teacher firefighter',
            'seasons spring summer autumn winter',
            'habitats animals forest ocean desert',
            'plants growing seeds life cycle',
            'maps directions near far left right',
            'friendship sharing kindness empathy',
            'simple machines wheel lever pulley',
            'healthy food nutrition body',
            'folk tales fables morals',
            'dinosaurs prehistoric animals',
            'solar system sun moon planets',
            'senses sight hearing touch smell',
            'rules community living together',
            'history past present future simple',
        ],
    },
    {
        'label':       'Year 2  (Building Blocks)',
        'description': 'Patterns, systems, first abstractions',
        'topics': [
            'ecosystems food chain predator prey',
            'water cycle rain evaporation clouds',
            'ancient civilisations Egypt Greece Rome',
            'human body organs heart lungs brain',
            'trade goods services money basic economics',
            'mythology legends gods heroes',
            'forces gravity push pull motion',
            'light shadow reflection colour spectrum',
            'habitats adaptation survival',
            'maps continents oceans geography',
            'simple electricity magnets energy',
            'stories characters conflict resolution plot',
            'immigration culture diversity community',
            'time measurement history timeline',
            'rocks minerals earth geology',
        ],
    },
    {
        'label':       'Year 3  (Expanding World)',
        'description': 'Cause and effect, interconnected systems',
        'topics': [
            'democracy government voting laws',
            'industrial revolution history technology',
            'photosynthesis cells biology basics',
            'fractions decimals number systems',
            'climate zones biomes world geography',
            'literature narrative metaphor symbolism',
            'ancient philosophy Socrates Aristotle',
            'chemistry elements atoms molecules',
            'human rights justice equality',
            'economics supply demand trade global',
            'evolution natural selection Darwin',
            'music rhythm harmony composition',
            'art movements impressionism expression',
            'world religions belief systems ethics',
            'environmental issues pollution conservation',
        ],
    },
    {
        'label':       'Year 4  (Systems Thinking)',
        'description': 'Abstract relationships, cause chains, multiple perspectives',
        'topics': [
            'algebra equations variables functions',
            'genetics heredity DNA evolution',
            'world wars history conflict diplomacy',
            'Renaissance science art humanism',
            'waves sound frequency vibration',
            'government systems democracy monarchy',
            'philosophy ethics morality duty',
            'statistics probability data analysis',
            'literature poetry imagery interpretation',
            'ecology biodiversity extinction',
            'economics capitalism markets inequality',
            'sociology culture identity belonging',
            'astronomy cosmology stars galaxies',
            'chemistry reactions bonds periodic table',
            'psychology emotion motivation behaviour',
        ],
    },
    {
        'label':       'Year 5  (Analytical Depth)',
        'description': 'Argument, evidence, multi-causal reasoning',
        'topics': [
            'calculus rates of change limits',
            'molecular biology protein synthesis',
            'political philosophy Locke Rousseau Marx',
            'thermodynamics energy entropy heat',
            'literary theory criticism analysis',
            'macroeconomics GDP inflation fiscal policy',
            'electromagnetism fields waves Maxwell',
            'cognitive psychology memory learning',
            'world history colonialism imperialism',
            'computer science algorithms data structures',
            'linguistics syntax semantics pragmatics',
            'ethics consequentialism deontology virtue',
            'neuroscience brain neural circuits',
            'quantum mechanics wave particle duality',
            'sociology institutions power inequality',
        ],
    },
    {
        'label':       'Year 6  (Specialist Knowledge)',
        'description': 'Discipline-specific depth and technical precision',
        'topics': [
            'machine learning neural networks deep learning',
            'philosophy of mind consciousness qualia',
            'topology manifolds abstract algebra',
            'molecular genetics CRISPR gene expression',
            'econometrics causal inference regression',
            'fluid dynamics turbulence chaos',
            'comparative literature postcolonial theory',
            'number theory prime cryptography',
            'immunology adaptive immune response',
            'political economy globalisation power',
            'formal logic proof theory model theory',
            'astrophysics dark matter relativity',
            'complex systems emergence self-organisation',
            'information theory entropy compression',
            'historiography primary sources interpretation',
        ],
    },
    {
        'label':       'Graduate  (Frontier)',
        'description': 'Open problems, uncertainty, cutting-edge research',
        'topics': [
            'consciousness hard problem qualia binding',
            'artificial general intelligence alignment',
            'quantum gravity string theory holography',
            'origin of life abiogenesis prebiotic chemistry',
            'free will determinism compatibilism',
            'foundations of mathematics Gödel incompleteness',
            'dark energy cosmological constant vacuum',
            'language models semantics grounding meaning',
            'social complexity emergence institutions',
            'epigenetics gene environment interaction',
            'consciousness integrated information theory',
            'causal inference counterfactuals intervention',
            'post-quantum cryptography lattice problems',
            'philosophy of physics time arrow entropy',
            'metacognition self-reference strange loops',
        ],
    },
]


# ======================================================================== #
# STAGE_CONFIG — per-stage source + readability configuration               #
# min_readability: Flesch Reading Ease floor (100=Dick&Jane, 0=PhD paper)  #
# sources:         which SOURCES keys to draw from, in priority order      #
# modifiers:       appended to queries to bias toward age-appropriate text  #
# ======================================================================== #

STAGE_CONFIG = [
    # Stage 0 — Ages 1-5 (Foundations)
    {
        'sources':         ['simple_wiki', 'gutenberg', 'web'],
        'modifiers':       ['children story', 'for kids', 'toddler', 'preschool', 'nursery'],
        'gutenberg_topic': 'children',
        'min_readability': 75,
        'reddit_time':     'all',
    },
    # Stage 1 — Year 1 (Early School)
    {
        'sources':         ['simple_wiki', 'gutenberg', 'wikipedia', 'web'],
        'modifiers':       ['for kids', 'elementary school', 'easy explanation', 'children'],
        'gutenberg_topic': 'juvenile',
        'min_readability': 65,
        'reddit_time':     'all',
    },
    # Stage 2 — Year 2 (Building Blocks)
    {
        'sources':         ['wikipedia', 'simple_wiki', 'gutenberg', 'web'],
        'modifiers':       ['explained for kids', 'middle school', 'introduction'],
        'gutenberg_topic': '',
        'min_readability': 52,
        'reddit_time':     'all',
    },
    # Stage 3 — Year 3 (Expanding World)
    {
        'sources':         ['wikipedia', 'web', 'reddit', 'gutenberg'],
        'modifiers':       ['explained', 'overview', 'introduction to'],
        'gutenberg_topic': '',
        'min_readability': 42,
        'reddit_time':     'year',
    },
    # Stage 4 — Year 4 (Systems Thinking)
    {
        'sources':         ['wikipedia', 'reddit', 'web', 'gutenberg'],
        'modifiers':       ['overview', 'explained'],
        'gutenberg_topic': '',
        'min_readability': 32,
        'reddit_time':     'year',
    },
    # Stage 5 — Year 5 (Analytical Depth)
    {
        'sources':         ['wikipedia', 'web', 'reddit', 'arxiv', 'openalex'],
        'modifiers':       [],
        'gutenberg_topic': '',
        'min_readability': 20,
        'reddit_time':     'year',
    },
    # Stage 6 — Year 6 (Specialist Knowledge)
    {
        'sources':         ['wikipedia', 'arxiv', 'openalex', 'web'],
        'modifiers':       [],
        'gutenberg_topic': '',
        'min_readability': 0,
        'reddit_time':     'year',
    },
    # Stage 7 — Graduate (Frontier)
    {
        'sources':         ['arxiv', 'openalex', 'wikipedia', 'web'],
        'modifiers':       [],
        'gutenberg_topic': '',
        'min_readability': 0,
        'reddit_time':     'year',
    },
]


# ======================================================================== #
# STAGE_TARGETS — minimum nodes + edges to be "on track" at each stage     #
# Used by Block I (assessor) to compute the breadth + depth grades.        #
# ======================================================================== #

STAGE_TARGETS = {
    0: {'nodes':   80,  'edges':   60,  'label': 'Ages 1–5'},
    1: {'nodes':  200,  'edges':  180,  'label': 'Year 1'},
    2: {'nodes':  400,  'edges':  400,  'label': 'Year 2'},
    3: {'nodes':  700,  'edges':  750,  'label': 'Year 3'},
    4: {'nodes': 1100,  'edges': 1300,  'label': 'Year 4'},
    5: {'nodes': 1600,  'edges': 2000,  'label': 'Year 5'},
    6: {'nodes': 2200,  'edges': 3000,  'label': 'Year 6'},
    7: {'nodes': 3000,  'edges': 4500,  'label': 'Graduate'},
}
