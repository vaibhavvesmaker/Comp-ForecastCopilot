"""
Thin wrapper around the Anthropic API client.

Kept as its own module so tests can mock ONE thing —
`AnthropicNarrativeClient.generate` — instead of reaching into the
`anthropic` SDK's internals. This is also the only place in
app/narrative/ that imports the `anthropic` package or reads
ANTHROPIC_API_KEY from the environment; everything else in the
narrative layer talks to this class, never to the SDK directly.
"""

from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024


class NarrativeConfigError(Exception):
    """Raised when the narrative layer can't be configured (e.g. missing API key)."""
    pass


class AnthropicNarrativeClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise NarrativeConfigError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and set it, "
                "or pass api_key= explicitly."
            )
        self.model = model
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def generate(self, system: str, user_prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
