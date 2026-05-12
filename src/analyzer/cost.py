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
    import ssl
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "token-tracker/0.1"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except ssl.SSLCertVerificationError:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "token-tracker/0.1"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())

    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(data, f)
    except OSError:
        pass

    return data


def _fallback_pricing() -> dict:
    return {
        # --- Anthropic Claude ---
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
        # --- OpenAI GPT-5 ---
        "gpt-5": {
            "input_cost_per_token": 1.25e-6,
            "output_cost_per_token": 10e-6,
        },
        "gpt-5.2-codex": {
            "input_cost_per_token": 1.75e-6,
            "output_cost_per_token": 14e-6,
        },
        "gpt-5.3-codex": {
            "input_cost_per_token": 1.75e-6,
            "output_cost_per_token": 14e-6,
        },
        "gpt-5.3-codex-spark": {
            "input_cost_per_token": 1.75e-6,
            "output_cost_per_token": 14e-6,
        },
        "gpt-5.4": {
            "input_cost_per_token": 2.5e-6,
            "output_cost_per_token": 15e-6,
        },
        "gpt-5.4-mini": {
            "input_cost_per_token": 0.75e-6,
            "output_cost_per_token": 4.5e-6,
        },
        "gpt-5.5": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 30e-6,
        },
        # --- 阿里 Qwen ---
        "qwen/qwen3.6-plus": {
            "input_cost_per_token": 0.4e-6,
            "output_cost_per_token": 1.2e-6,
        },
        "qwen-max": {
            "input_cost_per_token": 1.6e-6,
            "output_cost_per_token": 6.4e-6,
        },
        "qwen-plus": {
            "input_cost_per_token": 0.4e-6,
            "output_cost_per_token": 1.2e-6,
        },
        "qwen-turbo": {
            "input_cost_per_token": 0.05e-6,
            "output_cost_per_token": 0.2e-6,
        },
        # --- DeepSeek ---
        "deepseek-v4-flash": {
            "input_cost_per_token": 0.14e-6,
            "output_cost_per_token": 0.28e-6,
            "cache_read_input_token_cost": 0.0028e-6,
        },
        "deepseek-v4-pro": {
            "input_cost_per_token": 1.74e-6,
            "output_cost_per_token": 3.48e-6,
            "cache_read_input_token_cost": 0.0145e-6,
        },
        "deepseek-chat": {
            "input_cost_per_token": 0.14e-6,
            "output_cost_per_token": 0.28e-6,
        },
        "deepseek-reasoner": {
            "input_cost_per_token": 0.14e-6,
            "output_cost_per_token": 0.28e-6,
        },
        # --- Kimi / Moonshot ---
        "kimi-k2": {
            "input_cost_per_token": 0.55e-6,
            "output_cost_per_token": 2.20e-6,
        },
        "kimi-k2-thinking": {
            "input_cost_per_token": 0.55e-6,
            "output_cost_per_token": 2.20e-6,
        },
        "kimi-k2-turbo": {
            "input_cost_per_token": 1.10e-6,
            "output_cost_per_token": 8.00e-6,
        },
        "kimi-k2.5": {
            "input_cost_per_token": 0.60e-6,
            "output_cost_per_token": 3.00e-6,
        },
        "moonshot-v1": {
            "input_cost_per_token": 0.55e-6,
            "output_cost_per_token": 2.20e-6,
        },
        # --- 智谱 GLM ---
        "glm-5": {
            "input_cost_per_token": 0.95e-6,
            "output_cost_per_token": 3.15e-6,
        },
        "glm-4.7": {
            "input_cost_per_token": 0.60e-6,
            "output_cost_per_token": 2.20e-6,
        },
        "glm-4.6": {
            "input_cost_per_token": 0.60e-6,
            "output_cost_per_token": 2.20e-6,
        },
        "glm-4-flash": {
            "input_cost_per_token": 0.05e-6,
            "output_cost_per_token": 0.05e-6,
        },
        "glm-4": {
            "input_cost_per_token": 0.60e-6,
            "output_cost_per_token": 2.20e-6,
        },
        # --- 小米 MiMo ---
        "mimo-v2-flash": {
            "input_cost_per_token": 0.10e-6,
            "output_cost_per_token": 0.30e-6,
        },
        "mimo-v2.5-pro": {
            "input_cost_per_token": 1.0e-6,
            "output_cost_per_token": 3.0e-6,
            "cache_read_input_token_cost": 0.2e-6,
        },
        "mimo-v2.5-pro[1m]": {
            "input_cost_per_token": 2.0e-6,
            "output_cost_per_token": 6.0e-6,
            "cache_read_input_token_cost": 0.4e-6,
        },
    }
