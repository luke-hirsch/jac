MAX_ANSWER_LEN = 280  # one tweet; enforced in the serializer, hinted in the UI

# The system-default question pool: SEED SOURCE ONLY. At runtime the questions come from the
# PersonalityQuestion table (seeded from this list by `seed_system_defaults`), so a user's
# own additions live alongside these. `slug` is the stable answers-dict key — don't rename an
# existing one (it would orphan stored answers); add new entries at the end.
#
# The pool deliberately blends oblique/personal prompts (character) with work-values prompts
# (fit) — an all-oblique set pushed the distilled dossier too far from professional register;
# the work-values half pulls it back toward a hireable middle ground.
PERSONALITY_QUESTIONS = [
    # --- oblique / character ---
    {
        "slug": "excessive",
        "prompt": "What's something you do that others find slightly excessive?",
    },
    {
        "slug": "contrarian",
        "prompt": "What's a belief you hold that most people around you disagree with?",
    },
    {"slug": "flow", "prompt": "What do you lose track of time doing?"},
    {
        "slug": "bad_advice",
        "prompt": "What's a piece of common advice you think is just wrong?",
    },
    {
        "slug": "annoyance",
        "prompt": "What's the last thing that genuinely annoyed you — and why?",
    },
    {
        "slug": "small_pride",
        "prompt": "What's a small thing you're disproportionately proud of?",
    },
    {"slug": "most_yourself", "prompt": "When do you feel most like yourself?"},
    {"slug": "childhood", "prompt": "What did you want to be at ten years old?"},
    {"slug": "broken_rule", "prompt": "What's a rule you happily break?"},
    {
        "slug": "changed_mind",
        "prompt": "What's something you changed your mind about recently?",
    },
    {"slug": "admire", "prompt": "Who do you admire, and for what specifically?"},
    {"slug": "obsession", "prompt": "What problem can you not stop thinking about?"},
    # --- work values / fit (balancing set) ---
    {
        "slug": "roll_up_sleeves",
        "prompt": "What kind of problem makes you want to roll up your sleeves and dig in?",
    },
    {
        "slug": "best_environment",
        "prompt": "In what kind of environment do you do your best work?",
    },
    {
        "slug": "proud_project",
        "prompt": "What's a project you're proud of — and what was your part in it?",
    },
    {
        "slug": "collaborate",
        "prompt": "How do you work best with a team?",
    },
    {
        "slug": "good_manager",
        "prompt": "What would a good manager understand about how you work?",
    },
    {
        "slug": "worth_joining",
        "prompt": "Beyond the job title, what makes a company worth joining?",
    },
]
