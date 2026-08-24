"""Talking to the Claude API. Imported lazily, and only when the assist is on."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..logging_setup import get as get_logger
from .prompt import build_system_prompt, build_user_message, response_schema

log = get_logger("llm.client")

#: USD per million tokens, for the running cost cap. Approximate by design --
#: it exists to stop a runaway bill, not to bill anyone.
PRICING = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
DEFAULT_PRICING = (5.0, 25.0)


@dataclass
class LLMResult:
    ok: bool
    results: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def classify_batch(
    *,
    api_key: str,
    model: str,
    items: list[dict],
    categories: list[dict],
    example_rules: list[dict],
    timeout: float = 60.0,
) -> LLMResult:
    try:
        import anthropic
    except ImportError:
        return LLMResult(False, error="the anthropic package is not installed")

    category_keys = [c["key"] for c in categories]
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=build_system_prompt(categories, example_rules),
            messages=[{"role": "user", "content": build_user_message(items)}],
            output_config={
                "effort": "low",
                "format": {
                    "type": "json_schema",
                    "schema": response_schema(category_keys),
                },
            },
        )
    except anthropic.NotFoundError as exc:
        return LLMResult(False, error=f"model {model} is not available to this key: {exc}")
    except anthropic.RateLimitError as exc:
        return LLMResult(False, error=f"rate limited: {exc}")
    except anthropic.APIStatusError as exc:
        return LLMResult(False, error=f"API error {exc.status_code}: {exc}")
    except anthropic.APIConnectionError as exc:
        return LLMResult(False, error=f"could not reach the API: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        return LLMResult(False, error=f"unexpected error: {exc}")

    return _parse(response, model)


def _parse(response, model: str) -> LLMResult:
    import json

    text = ""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "text":
            text = block.text
            break
    if not text:
        return LLMResult(False, error="empty response")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return LLMResult(False, error=f"could not parse the response: {exc}")

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return LLMResult(
        ok=True,
        results=list(payload.get("results") or []),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost(model, input_tokens, output_tokens),
    )
