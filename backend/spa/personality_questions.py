MAX_ANSWER_LEN = 280  # one tweet; enforced in the serializer, hinted in the UI

PERSONALITY_QUESTIONS = [
    {
        "id": "excessive",
        "prompt": "What's something you do that others find slightly excessive?",
    },
    {
        "id": "contrarian",
        "prompt": "What's a belief you hold that most people around you disagree with?",
    },
    {"id": "flow", "prompt": "What do you lose track of time doing?"},
    {
        "id": "bad_advice",
        "prompt": "What's a piece of common advice you think is just wrong?",
    },
    {
        "id": "annoyance",
        "prompt": "What's the last thing that genuinely annoyed you — and why?",
    },
    {
        "id": "small_pride",
        "prompt": "What's a small thing you're disproportionately proud of?",
    },
    {"id": "most_yourself", "prompt": "When do you feel most like yourself?"},
    {"id": "childhood", "prompt": "What did you want to be at ten years old?"},
    {"id": "broken_rule", "prompt": "What's a rule you happily break?"},
    {
        "id": "changed_mind",
        "prompt": "What's something you changed your mind about recently?",
    },
    {"id": "admire", "prompt": "Who do you admire, and for what specifically?"},
    {"id": "obsession", "prompt": "What problem can you not stop thinking about?"},
]
_QUESTION_LABEL = {q["id"]: q["prompt"] for q in PERSONALITY_QUESTIONS}
