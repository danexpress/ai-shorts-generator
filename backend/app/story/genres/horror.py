from . import Genre

GENRE = Genre(
    id="horror",
    label="Horror",
    guidance=(
        "Ground the story in an ordinary place and moment, then let one wrong detail grow. "
        "Use dread over gore: what is heard, almost seen, or missing. End on a final "
        "twist or image that lingers. No graphic violence."
    ),
    default_tone="slow, unsettling",
    moods=("calm", "uneasy", "dread", "shock"),
    order=20,
    default_background="dusk",
    image_style="dark atmospheric horror film still, desaturated, cold light, unsettling empty spaces",
)
