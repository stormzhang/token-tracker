import json
import os
import urllib.request
from pathlib import Path

from ..adapters.types import UsageEntry

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
CACHE_PATH = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "pricing_cache.json"

_pricing: dict | None = None


def get_pricing() -> dict:
    global _pricing
    if _pricing is not None:
        return _pricing
    _pricing = _load_pricing()
    return _pricing


def calculate_cost(entry: UsageEntry) -> float:
    if entry.cost_usd is not None:
        return entry.cost_usd

    pricing = get_pricing()
    model_key = _resolve_model_key(entry.model, pricing)
    if model_key is None:
        return 0.0

    info = pricing[model_key]
    input_cost = info.get("input_cost_per_token", 0)
    output_cost = info.get("output_cost_per_token", 0)
    cache_creation_cost = info.get("cache_creation_input_token_cost", input_cost * 1.25)
    cache_read_cost = info.get("cache_read_input_token_cost", input_cost * 0.1)

    return (
        entry.input_tokens * input_cost
        + entry.output_tokens * output_cost
        + entry.cache_creation_tokens * cache_creation_cost
        + entry.cache_read_tokens * cache_read_cost
    )


def _resolve_model_key(model: str, pricing: dict) -> str | None:
    if model in pricing:
        return model

    for key in pricing:
        if model in key or key in model:
            return key

    model_lower = model.lower()
    for key in pricing:
        if model_lower in key.lower() or key.lower() in model_lower:
            return key

    return None


def _load_pricing() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    try:
        return _fetch_and_cache()
    except Exception:
        return _fallback_pricing()


def _fetch_and_cache() -> dict:
    req = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "token-tracker/0.1"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(data, f)
    except OSError:
        pass

    return data


def _fallback_pricing() -> dict:
    return {
        "claude-opus-4-6": {
            "input_cost_per_token": 15e-6,
            "output_cost_per_token": 75e-6,
            "cache_creation_input_token_cost": 18.75e-6,
            "cache_read_input_token_cost": 1.5e-6,
        },
        "claude-opus-4-7": {
            "input_cost_per_token": 15e-6,
            "output_cost_per_token": 75e-6,
            "cache_creation_input_token_cost": 18.75e-6,
            "cache_read_input_token_cost": 1.5e-6,
        },
        "claude-sonnet-4-6": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 15e-6,
            "cache_creation_input_token_cost": 3.75e-6,
            "cache_read_input_token_cost": 0.3e-6,
        },
        "claude-haiku-4-5-20251001": {
            "input_cost_per_token": 0.8e-6,
            "output_cost_per_token": 4e-6,
            "cache_creation_input_token_cost": 1e-6,
            "cache_read_input_token_cost": 0.08e-6,
        },
    }
