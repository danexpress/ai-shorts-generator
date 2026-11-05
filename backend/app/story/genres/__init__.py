"""Genre templates. Each module in this package defines `GENRE = Genre(...)`.

To add a genre, add a file here (see README: "Adding a genre template"); it is
discovered automatically and offered in the Story tab, ordered by `order`.
"""

import importlib
import pkgutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Genre:
    id: str
    label: str
    # What makes a strong story in this genre; sent to the model with every script.
    guidance: str
    default_tone: str
    # Suggested scene moods (later used to pick music and visual style).
    moods: tuple[str, ...]
    order: int = 100
    # Bundled background preselected for this genre (see app/story/visuals.py).
    default_background: str = "midnight"

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "defaultTone": self.default_tone,
            "moods": list(self.moods),
            "defaultBackground": self.default_background,
        }


def _load() -> dict[str, Genre]:
    found = {}
    for module in pkgutil.iter_modules(__path__):
        genre = getattr(importlib.import_module(f"{__name__}.{module.name}"), "GENRE", None)
        if not isinstance(genre, Genre):
            raise TypeError(f"Story genre module {module.name} must define GENRE = Genre(...)")
        if genre.id in found:
            raise ValueError(f"Duplicate story genre id {genre.id!r}")
        found[genre.id] = genre
    return dict(sorted(found.items(), key=lambda item: (item[1].order, item[0])))


GENRES = _load()
