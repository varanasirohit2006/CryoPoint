"""Provider selection is explicit; unavailable providers never fall back."""

import hashlib
import os

from jit_ui.config.settings import Settings
from jit_ui.providers.base import ProviderError, VLMProvider
from jit_ui.providers.rate_limit import RequestPacer

_gemini_pacers: dict[tuple[str, int], RequestPacer] = {}


def create_provider(name: str, settings: Settings) -> VLMProvider:
    if name == "gemini":
        from jit_ui.providers.gemini import GeminiProvider

        key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        identity = (hashlib.sha256(key.encode()).hexdigest(), settings.gemini_rpm)
        pacer = _gemini_pacers.setdefault(identity, RequestPacer(settings.gemini_rpm))
        return GeminiProvider(key, settings.gemini_model, settings.gemini_rpm, pacer=pacer)
    if name == "ollama":
        from jit_ui.providers.ollama import OllamaProvider

        return OllamaProvider(settings.ollama_url, settings.ollama_model, settings.ollama_context,
                              max_output_tokens=settings.ollama_max_output_tokens,
                              image_max_width=settings.ollama_image_max_width)
    if name == "fixture":
        from jit_ui.providers.fixture import FixtureProvider

        return FixtureProvider()
    raise ProviderError(f"Unknown provider: {name}.", "UNKNOWN_PROVIDER")


__all__ = ["ProviderError", "VLMProvider", "create_provider"]
