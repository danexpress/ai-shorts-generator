from . import Genre

GENRE = Genre(
    id="mystery",
    label="Mystery",
    guidance=(
        "Open on a concrete, unexplained detail. Plant one or two clues the viewer can "
        "notice, raise the stakes with each scene, and end with a reveal that the clues "
        "make fair in hindsight."
    ),
    default_tone="suspenseful and quiet",
    moods=("eerie", "curious", "tense", "revealing"),
    order=10,
    image_style="moody cinematic film still, fog and deep shadows, muted teal and amber palette",
)
