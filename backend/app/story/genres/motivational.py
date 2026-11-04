from . import Genre

GENRE = Genre(
    id="motivational",
    label="Motivational",
    guidance=(
        "Tell one person's turning point as a story, not a list of tips: a low moment, a "
        "choice, a cost, and what changed. Keep it specific and believable, and end on "
        "one line the viewer can carry with them."
    ),
    default_tone="warm and determined",
    moods=("low", "reflective", "rising", "triumphant"),
    order=30,
)
