from . import Genre

GENRE = Genre(
    id="history",
    label="History",
    guidance=(
        "Tell a true-to-the-period story from a vivid moment in history, through the eyes "
        "of an unnamed ordinary person (a sailor, a clerk, a child). Keep facts about the "
        "era accurate and avoid invented statistics. End with what happened next or why "
        "it still matters. Refer to famous figures by role, not by name."
    ),
    default_tone="vivid and authoritative",
    moods=("grounded", "tense", "dramatic", "reflective"),
    order=40,
)
