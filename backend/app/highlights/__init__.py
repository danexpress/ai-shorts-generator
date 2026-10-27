from .llm import ClaudeHighlightModel, HighlightUnavailable
from .prompt import PROMPT_VERSION
from .service import HighlightFailed, NoHighlights, find_highlights
from .validate import InvalidOutput


def build_model(settings) -> ClaudeHighlightModel:
    return ClaudeHighlightModel(
        settings.highlight_model, settings.highlight_effort, settings.highlight_fallbacks
    )


__all__ = [
    "PROMPT_VERSION",
    "ClaudeHighlightModel",
    "HighlightFailed",
    "HighlightUnavailable",
    "InvalidOutput",
    "NoHighlights",
    "build_model",
    "find_highlights",
]
