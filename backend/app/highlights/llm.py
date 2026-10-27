"""Claude API client for highlight detection, with structured JSON output.

Each call logs latency, token usage and estimated cost. The model is configurable
with HIGHLIGHT_MODEL; tests inject a fake with the same `complete` method.
"""

import json
import logging
import time
from dataclasses import dataclass, field

from .validate import InvalidOutput

logger = logging.getLogger(__name__)

# USD per million tokens: (input, output, cache read). Cache writes cost 1.25x input;
# cache reads use the published rate, else 0.1x input. Used for cost logging only.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
# Models that accept the server-side `fallbacks: "default"` refusal fallback.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class HighlightUnavailable(RuntimeError):
    """The model could not be reached or the request was rejected (retryable job)."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0
    cost: float | None = 0.0
    models: set = field(default_factory=set)

    def add(self, model: str, usage, seconds: float):
        read = getattr(usage, "cache_read_input_tokens", None) or 0
        write = getattr(usage, "cache_creation_input_tokens", None) or 0
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += read
        self.cache_write_tokens += write
        self.seconds += seconds
        self.models.add(model)
        price = PRICES.get(model)
        if price is None or self.cost is None:
            self.cost = None  # unknown model: report tokens only
        else:
            prompt, completion, cached = price
            self.cost += (
                usage.input_tokens * prompt
                + write * prompt * 1.25
                + read * cached
                + usage.output_tokens * completion
            ) / 1_000_000

    def summary(self) -> str:
        cost = f"${self.cost:.4f}" if self.cost is not None else "cost unknown"
        return (
            f"{self.calls} call(s), {self.seconds:.1f}s, {self.input_tokens} in "
            f"(+{self.cache_read_tokens} cached, +{self.cache_write_tokens} cache write), "
            f"{self.output_tokens} out, {cost}"
        )


class ClaudeHighlightModel:
    def __init__(self, model: str, effort: str = "high", fallbacks: str = "default", client=None):
        self.model, self.effort, self.fallbacks = model, effort, fallbacks
        self._client = client
        self.usage = Usage()

    @property
    def client(self):
        if self._client is None:
            import anthropic

            # Credentials: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
            self._client = anthropic.Anthropic(max_retries=3)
        return self._client

    def complete(self, system: str, cached: str, variable: str, schema: dict) -> dict:
        """One structured-output request. `cached` (the transcript) is a stable,
        cacheable prefix; `variable` (guidance, retry feedback) follows it."""
        import anthropic

        options = {}
        if self.fallbacks == "default" and self.model in FALLBACK_MODELS:
            options = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        started = time.monotonic()
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": cached,
                                "cache_control": {"type": "ephemeral"},
                            },
                            {"type": "text", "text": variable},
                        ],
                    }
                ],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                **options,
            )
        except anthropic.AuthenticationError as exc:
            raise HighlightUnavailable(
                "Claude API authentication failed; set ANTHROPIC_API_KEY or run `ant auth login`."
            ) from exc
        except anthropic.BadRequestError as exc:
            raise HighlightUnavailable(f"Claude API rejected the request: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise HighlightUnavailable("Claude API rate limit reached; retry later.") from exc
        except anthropic.APIStatusError as exc:
            raise HighlightUnavailable(f"Claude API error {exc.status_code}.") from exc
        except anthropic.APIConnectionError as exc:
            raise HighlightUnavailable("Could not reach the Claude API.") from exc
        elapsed = time.monotonic() - started
        served_by = getattr(response, "model", None) or self.model
        self.usage.add(served_by, response.usage, elapsed)
        logger.info(
            "Highlight LLM call: %s, %.1fs, %d in / %d out tokens (%d cached), stop=%s, req=%s",
            served_by,
            elapsed,
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", None) or 0,
            response.stop_reason,
            getattr(response, "_request_id", None),
        )
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise InvalidOutput(f"The model declined the request (category: {category}).")
        if response.stop_reason == "max_tokens":
            raise InvalidOutput("The answer was cut off; return fewer, shorter fields.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise InvalidOutput("No JSON answer was returned.")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise InvalidOutput("The answer was not valid JSON.") from exc
