"""
30 deliberate A/B evaluation prompts.
10 LOW  — factual, unambiguous, single-concept
10 MED  — interesting but not clearly tensioned
10 HIGH — paradoxes, creative tensions, contradictions

Used by the A/B page blind evaluation mode.
"""

EVAL_PROMPTS = [
    # ------------------------------------------------------------------ #
    # LOW ambiguity — factual, closed, definitional                        #
    # ------------------------------------------------------------------ #
    {"id": "L01", "expected": "low",
     "prompt": "What is the capital of France?"},
    {"id": "L02", "expected": "low",
     "prompt": "How does photosynthesis work?"},
    {"id": "L03", "expected": "low",
     "prompt": "What year was the Eiffel Tower built?"},
    {"id": "L04", "expected": "low",
     "prompt": "How many bones are in the adult human body?"},
    {"id": "L05", "expected": "low",
     "prompt": "What is the boiling point of water at sea level?"},
    {"id": "L06", "expected": "low",
     "prompt": "Who wrote Romeo and Juliet?"},
    {"id": "L07", "expected": "low",
     "prompt": "What is the speed of light in a vacuum?"},
    {"id": "L08", "expected": "low",
     "prompt": "List the planets in the solar system in order from the sun."},
    {"id": "L09", "expected": "low",
     "prompt": "What does DNA stand for and what does it do?"},
    {"id": "L10", "expected": "low",
     "prompt": "How do vaccines work?"},

    # ------------------------------------------------------------------ #
    # MEDIUM ambiguity — open but not tensioned                            #
    # ------------------------------------------------------------------ #
    {"id": "M01", "expected": "medium",
     "prompt": "How does memory change as we age?"},
    {"id": "M02", "expected": "medium",
     "prompt": "What makes a good leader?"},
    {"id": "M03", "expected": "medium",
     "prompt": "How do habits form and what makes them hard to break?"},
    {"id": "M04", "expected": "medium",
     "prompt": "What is the difference between knowledge and wisdom?"},
    {"id": "M05", "expected": "medium",
     "prompt": "How does music affect human emotions?"},
    {"id": "M06", "expected": "medium",
     "prompt": "What makes something beautiful?"},
    {"id": "M07", "expected": "medium",
     "prompt": "How do cultures shape individual identity?"},
    {"id": "M08", "expected": "medium",
     "prompt": "What is the relationship between risk and creativity?"},
    {"id": "M09", "expected": "medium",
     "prompt": "How does context change the meaning of words?"},
    {"id": "M10", "expected": "medium",
     "prompt": "Explain the relationship between language and thought."},

    # ------------------------------------------------------------------ #
    # HIGH ambiguity — paradoxes, tensions, contradictions                 #
    # ------------------------------------------------------------------ #
    {"id": "H01", "expected": "high",
     "prompt": "Can something be true and false at the same time?"},
    {"id": "H02", "expected": "high",
     "prompt": "Is silence a form of speech?"},
    {"id": "H03", "expected": "high",
     "prompt": "Explain how love and logic can guide the same decision in opposite directions."},
    {"id": "H04", "expected": "high",
     "prompt": "What does it mean for something to be alive but not conscious?"},
    {"id": "H05", "expected": "high",
     "prompt": "Design a city that feels futuristic and ancient at the same time."},
    {"id": "H06", "expected": "high",
     "prompt": "Is free will compatible with a deterministic universe?"},
    {"id": "H07", "expected": "high",
     "prompt": "Can you be genuinely loyal to two contradictory beliefs?"},
    {"id": "H08", "expected": "high",
     "prompt": "Where does intuition end and rationality begin — and who decides?"},
    {"id": "H09", "expected": "high",
     "prompt": "Write about a machine that learns to grieve something it never had."},
    {"id": "H10", "expected": "high",
     "prompt": "Is it ethical to lie to protect someone from a truth that would destroy them?"},
]
