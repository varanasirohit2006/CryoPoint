"""Gemini vision with per-adapter pacing and explicit, bounded 429 handling."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

from jit_ui.domain.models import ActionFrame, Observation, ProviderResponse, TaskRequest
from jit_ui.providers.base import Decision, ProviderError
from jit_ui.providers.action_catalog import build_catalog, parse_selection, selection_prompt, selection_schema
from jit_ui.providers.rate_limit import RequestPacer, retry_after_seconds


def gemini_decision_schema() -> dict[str, Any]:
    """Gemini's native Schema excludes JSON Schema additionalProperties.

    Keep the strict Pydantic contract when parsing; remove only the unsupported
    outbound field so extra model keys still cannot reach the browser.
    """
    def compatible(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: compatible(item) for key, item in value.items() if key != "additionalProperties"}
        if isinstance(value, list):
            return [compatible(item) for item in value]
        return value

    schema = Decision.model_json_schema()
    schema["$defs"]["ActionSpec"]["properties"].pop("url_refs", None)
    schema["$defs"]["ConditionSpec"]["properties"].pop("expected_template", None)
    return compatible(schema)


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite", requests_per_minute: int = 12, *, client: Any = None, pacer: RequestPacer | None = None, sleep: Any = asyncio.sleep) -> None:
        if not api_key and client is None:
            raise ProviderError("Set JIT_GEMINI_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY to enable Gemini.", "MISSING_API_KEY")
        if client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise ProviderError("Install the google-genai dependency to enable Gemini.", "PROVIDER_DEPENDENCY_MISSING") from exc
            # Disable SDK retries so each actual request is paced and accounted here.
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)))
        self.client = client
        self.model = model
        self.pacer = pacer or RequestPacer(requests_per_minute)
        self._sleep = sleep
        self.attempts_total = 0
        self.remaining_attempts: int | None = None

    async def decide(self, observation: Observation, task: TaskRequest, history: list[ActionFrame], instruction: str = "") -> ProviderResponse:
        try:
            from google.genai import types
        except ImportError as exc:
            raise ProviderError("Install the google-genai dependency to enable Gemini.", "PROVIDER_DEPENDENCY_MISSING") from exc
        catalog = build_catalog(observation, task)
        contents: list[Any] = [selection_prompt(observation, task, history, instruction, catalog)]
        if observation.screenshot_base64:
            contents.append(types.Part.from_bytes(data=base64.b64decode(observation.screenshot_base64, validate=True), mime_type="image/jpeg"))
        parsed, usage = await self._generate(contents, selection_schema(task, catalog, strict_completion=True),
                                             lambda text: parse_selection(text, catalog, task))
        decision, candidate_id = parsed
        return usage.model_copy(update={"action": decision.action, "done": decision.done,
            "candidate_id": candidate_id, "completion_conditions": decision.completion_conditions})

    async def interpret_task(self, task):
        from jit_ui.providers.task_interpretation import TaskInterpretation, interpretation_prompt, parse_interpretation
        return await self._generate([interpretation_prompt(task)], TaskInterpretation.model_json_schema(), parse_interpretation)

    async def _generate(self, contents, schema, parser):
        from google.genai import types

        started = time.perf_counter()
        max_attempts = min(3, self.remaining_attempts) if self.remaining_attempts is not None else 3
        if max_attempts < 1:
            raise ProviderError("Model attempt budget is exhausted.", "MODEL_BUDGET_EXCEEDED")
        for attempt in range(max_attempts):
            await self.pacer.acquire()
            self.attempts_total += 1
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=schema, temperature=0, max_output_tokens=768),
                )
                usage = getattr(response, "usage_metadata", None)
                try:
                    parsed = parser(getattr(response, "text", None) or "")
                except ProviderError as exc:
                    exc.usage = {"input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                                 "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0)}
                    exc.usage_known = usage is not None and attempt == 0
                    raise
                return parsed, ProviderResponse(
                    model=self.model,
                    input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                    output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt + 1,
                    usage_known=usage is not None and attempt == 0,
                )
            except ProviderError:
                raise
            except Exception as exc:
                status = getattr(exc, "code", None) or getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                if str(status) in {"500", "502", "503", "504"}:
                    if attempt < max_attempts - 1:
                        await self._sleep(float(2 ** attempt))
                        continue
                    raise ProviderError("Gemini is temporarily unavailable after bounded retries. Retry later or explicitly select Ollama.",
                                        "PROVIDER_TEMPORARILY_UNAVAILABLE") from exc
                if str(status) != "429":
                    if str(status) == "400":
                        details = str(getattr(exc, "details", "")).casefold()
                        if "api_key_invalid" in details or "api key not valid" in details:
                            raise ProviderError("Gemini rejected the configured API key. Set a valid JIT_GEMINI_API_KEY.", "INVALID_API_KEY") from exc
                        if "response_schema" in details or "response schema" in details:
                            raise ProviderError("Gemini rejected the structured action schema. Check provider SDK and model compatibility.", "PROVIDER_SCHEMA_REJECTED") from exc
                        raise ProviderError("Gemini rejected the request (400). Check the selected model's vision and structured output support.", "PROVIDER_BAD_REQUEST") from exc
                    code = "MODEL_UNAVAILABLE" if str(status) == "404" else "PROVIDER_UNAVAILABLE"
                    raise ProviderError(f"Gemini {self.model} request failed ({status or type(exc).__name__}). Check model access and provider configuration.", code) from exc
                headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                delay = retry_after_seconds(headers.get("Retry-After") or headers.get("retry-after"))
                details = getattr(exc, "details", None)
                if delay is None and isinstance(details, dict):
                    error = details.get("error", details)
                    for detail in error.get("details", []) if isinstance(error, dict) else []:
                        raw = detail.get("retryDelay") if isinstance(detail, dict) else None
                        if raw:
                            delay = retry_after_seconds(str(raw).removesuffix("s"))
                            break
                delay = delay if delay is not None else float(5 * (2**attempt))
                if attempt == max_attempts - 1 or delay > 120:
                    raise ProviderError("Gemini rate limit reached; retries exhausted. Retry later or explicitly choose a configured Ollama model.", "RATE_LIMITED", delay) from exc
                await self._sleep(delay)
        raise ProviderError("Gemini retry limit reached.", "RATE_LIMITED")

    async def close(self) -> None:
        closer = getattr(getattr(self.client, "aio", None), "aclose", None)
        if closer:
            await closer()
