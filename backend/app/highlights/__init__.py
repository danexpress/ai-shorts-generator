from ..llm import ClaudeModel, InvalidOutput, LLMUnavailable
from .prompt import PROMPT_VERSION
from .service import HighlightFailed, NoHighlights, find_highlights


def build_model(settings) -> ClaudeModel:
    return ClaudeModel(
        settings.highlight_model, settings.highlight_effort, settings.highlight_fallbacks
    )


__all__ = [
    "PROMPT_VERSION",
    "ClaudeModel",
    "HighlightFailed",
    "InvalidOutput",
    "LLMUnavailable",
    "NoHighlights",
    "build_model",
    "find_highlights",
]
