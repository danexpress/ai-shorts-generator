from . import Genre

GENRE = Genre(
    id="funny",
    label="Funny",
    guidance=(
        "Set up an everyday situation that escalates one logical step at a time into "
        "absurdity. Keep jokes short and spoken; the rhythm matters. End on a punchline "
        "or callback that reframes the opening."
    ),
    default_tone="playful and deadpan",
    moods=("light", "awkward", "chaotic", "punchline"),
    order=50,
    default_background="deep-sea",
)
