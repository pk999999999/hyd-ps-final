"""One explicit text-generation boundary for all supported model providers.

The `auto` setting selects a configured provider once. A failed request never
falls through to a different provider, which could disclose customer data to an
unexpected service.
"""
from dataclasses import dataclass
import json
import httpx
from pydantic import TypeAdapter, ValidationError

from app.core.config import get_settings


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    structured_output: bool
    tool_calling: bool


_CAPABILITIES = {
    "openai": ProviderCapabilities("openai", True, True),
    "anthropic": ProviderCapabilities("anthropic", True, True),
    "groq": ProviderCapabilities("groq", False, False),
    "gemini": ProviderCapabilities("gemini", True, True),
    "openrouter": ProviderCapabilities("openrouter", False, False),
    "compatible": ProviderCapabilities("compatible", False, False),
}


def selected_provider(settings=None):
    settings = settings or get_settings()
    if settings.llm_provider != "auto":
        return settings.llm_provider
    for name, configured in (
        ("openrouter", settings.openrouter_api_key), ("groq", settings.groq_api_key),
        ("gemini", settings.gemini_api_key), ("openai", settings.openai_api_key),
        ("anthropic", settings.anthropic_api_key),
        ("compatible", settings.llm_model and settings.llm_base_url),
    ):
        if configured:
            return name
    raise ProviderError("No reasoning provider is configured. Set LLM_PROVIDER and its key/model, or enable demo mode.")


def get_provider_capabilities(settings=None):
    return _CAPABILITIES[selected_provider(settings)]


def validate_provider_configuration(settings=None):
    settings = settings or get_settings()
    name = selected_provider(settings)
    credentials = {
        "openai": (settings.openai_api_key, settings.openai_model),
        "anthropic": (settings.anthropic_api_key, settings.anthropic_model),
        "groq": (settings.groq_api_key, settings.groq_model),
        "gemini": (settings.gemini_api_key, settings.gemini_model),
        "openrouter": (settings.openrouter_api_key, settings.openrouter_model),
        "compatible": (settings.llm_base_url, settings.llm_model),
    }
    key, model = credentials[name]
    if not key or not model or "test_key_here" in str(key):
        raise ProviderError(f"{name.title()} is missing its API key or model configuration.")
    return name, model


async def _post(url, headers, payload, timeout):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise ProviderError("The reasoning provider timed out. Retry the task later.") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            raise ProviderError("The reasoning provider rate limit was reached. Retry later.") from exc
        if code in (401, 403):
            raise ProviderError("The reasoning provider rejected its configured credentials.") from exc
        if code == 402:
            raise ProviderError("The reasoning provider has insufficient credits.") from exc
        raise ProviderError(f"The reasoning provider returned HTTP {code}.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("The reasoning provider is unavailable or returned malformed data.") from exc


async def generate_text(system_prompt: str, user_prompt: str, *, settings=None) -> str:
    settings = settings or get_settings()
    if settings.demo_mode:
        raise ProviderError("Live reasoning is disabled in demo mode; use the sample campaign workflow.")
    name, model = validate_provider_configuration(settings)
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    limit = settings.llm_max_output_tokens
    timeout = settings.llm_timeout_seconds
    if name == "anthropic":
        data = await _post("https://api.anthropic.com/v1/messages",
            {"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": limit, "system": system_prompt,
             "messages": [{"role": "user", "content": user_prompt}]}, timeout)
        content = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    elif name == "gemini":
        data = await _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": settings.gemini_api_key},
            {"system_instruction": {"parts": [{"text": system_prompt}]},
             "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
             "generationConfig": {"maxOutputTokens": limit}}, timeout)
        content = "".join(part.get("text", "") for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []))
    else:
        endpoints = {
            "openai": ("https://api.openai.com/v1/chat/completions", settings.openai_api_key),
            "groq": ("https://api.groq.com/openai/v1/chat/completions", settings.groq_api_key),
            "openrouter": ("https://openrouter.ai/api/v1/chat/completions", settings.openrouter_api_key),
            "compatible": (settings.llm_base_url.rstrip("/") + "/chat/completions", settings.llm_api_key),
        }
        url, key = endpoints[name]
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if name == "openrouter":
            headers.update({"HTTP-Referer": settings.public_base_url, "X-Title": settings.app_name})
        payload = {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": limit}
        if name == "openrouter":
            payload.update({"reasoning": {"effort": settings.llm_reasoning_effort},
                            "provider": {"sort": "price"}})
        data = await _post(url, headers, payload, timeout)
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        if not content and choice.get("finish_reason") == "length":
            raise ProviderError("The model used its token budget without producing a draft. Increase the output limit or retry.")
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("The reasoning provider returned an empty or unsupported response.")
    return content.strip()


async def generate_structured(system_prompt: str, user_prompt: str, schema, *, settings=None):
    """Validate JSON text with a Pydantic schema independent of provider dialect."""
    raw = await generate_text(system_prompt + "\nReturn only valid JSON.", user_prompt, settings=settings)
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return TypeAdapter(schema).validate_python(json.loads(clean))
    except (ValueError, ValidationError) as exc:
        raise ProviderError("The reasoning provider returned invalid structured output.") from exc
