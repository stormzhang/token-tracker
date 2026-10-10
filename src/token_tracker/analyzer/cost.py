import http.client
import json
import math
import os
import ssl
import sys
import time
import urllib.request
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError

from ..adapters.types import UsageEntry, UsageSegment

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
# 缓存放用户可写目录：包根目录在 site-packages 下是只读的，写失败会导致每次都联网
CACHE_DIR = Path(os.path.expanduser("~/.cache/token-tracker"))
CACHE_PATH = CACHE_DIR / "pricing_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 定价表 7 天过期，过期后尝试刷新（失败仍用旧缓存兜底）
_INSECURE_TLS_ENV = "TT_PRICING_INSECURE_TLS"

_pricing: dict | None = None
_warned_insecure = False

# 未知的新模型按系列退回最新已知价（这些 key 由 _fallback_pricing 保证存在）。
# codex- 兜底覆盖 Codex 内部虚拟 model（如 codex-auto-review，stop-time auto-review gate 用）
_FAMILY_FALLBACK = (
    ("claude-opus", "claude-opus-5-5"),
    ("claude-sonnet", "claude-sonnet-5-5"),
    ("claude-haiku", "claude-haiku-5-5"),
    ("claude-fable", "claude-fable-5-1"),
    ("claude-mythos", "claude-mythos-5-1"),
    # GPT-5.6 三档并列（sol/terra/luna 各自有内置价，dated/variant 靠前缀命中）；系列内
    # 未知新档（如假想 gpt-5.6-nova）退回旗舰 sol——新档价未知时退回最贵档，宁可高估不低估
    ("gpt-5.6", "gpt-5.6-sol"),
    ("codex-", "gpt-5.6-sol"),
    # 国产模型系列兜底：出新版本（如 GLM-4.8、Kimi K3）litellm 未收录时退回该系列最新已知价
    # Kimi Code 会话 wire 的模型 id 带 alias 前缀（kimi-code/k3），路由到官方 API 档（与 k2.6 价差 3 倍+）
    ("kimi-code/k3", "kimi-k3"),
    ("kimi", "kimi-k2.6"),
    ("moonshot-v", "moonshot-v1-128k"),
    ("glm-5", "glm-5.3"),
    ("glm-4", "glm-4.6"),
    ("qwen3-coder", "qwen3-coder-plus"),
    ("qwen3-max", "qwen-max"),
    ("doubao-seed", "doubao-seed-1-6"),
    ("doubao-1-5-pro", "doubao-1-5-pro-256k"),
    ("deepseek", "deepseek-v4-flash"),
    ("minimax", "MiniMax-M2"),
    ("mimo", "mimo-v2.5"),
    # Grok：退役 slug 按官方路由兜底（grok-code-* → build-0.1；grok-4-fast/4.1-fast/grok-3 等 → grok-4.3）
    ("grok-code", "grok-build-0.1"),
    ("grok-4", "grok-4.3"),
    ("grok", "grok-4.3"),
)

# LiteLLM 新模型常只有 provider/model 键，Agent 日志则只记 bare model id。
# 只对厂商官方 provider 做定向补全，避免同模型在第三方平台的加价项被误当成官方价。
_OFFICIAL_PROVIDER_PREFIXES = (
    ("grok-", ("xai/",)),
    ("glm-", ("zai/",)),
    ("qwen", ("dashscope/",)),
    ("kimi-", ("moonshot/",)),
    ("minimax-", ("minimax/",)),
)

_DEEPSEEK_NEW_PRICING_AT = datetime(2026, 8, 16, 16, 0, tzinfo=UTC)
_DEEPSEEK_V41_FLASH_AT = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
_CHINA_TZ = timezone(timedelta(hours=8))
# 2026 年国务院放假安排；峰谷价从 2026-08 生效，只需覆盖此后的中秋和国庆。
# https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202511/t20251104_4258838.html
# 后续年份需在官方公布后补充，不能从农历节日或调休工作日猜测计费日历。
_DEEPSEEK_HOLIDAYS = (
    (date(2026, 9, 25), date(2026, 9, 27)),
    (date(2026, 10, 1), date(2026, 10, 7)),
)
_GEMINI_FLASH_PRICE_CHANGE_AT = datetime(2027, 1, 1, tzinfo=UTC)
_GEMINI_FLASH_MODELS = {"gemini-3.6-flash", "gemini-3.8-flash"}
_DEEPSEEK_V41_FLASH_MODELS = {
    "deepseek-flash",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
}
_DEEPSEEK_V4_PRICING_KEYS = {
    "deepseek-flash",
    "deepseek/deepseek-flash",
    "deepseek-v4-flash",
    "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
    "deepseek/deepseek-v4-flash-vision-exp",
    "deepseek-chat",
    "deepseek-reasoner",
}

# 解析不到定价的模型只提示一次，避免聚合时每条 entry 刷屏
_warned_unknown_models: set[str] = set()

# model → 解析出的定价 key。非精确命中要线性扫全表（litellm 数千 key），逐 entry 调用必须记忆化。
# 命中后还校验 key 仍在当前 pricing 里（测试会整表替换 _pricing），失效则重算。
_model_key_cache: dict[str, str | None] = {}


def get_pricing() -> dict:
    global _pricing
    if _pricing is not None:
        return _pricing
    # 以 litellm 数据为准，_fallback_pricing 作为已知价底座补 litellm 尚未收录的新模型（如最新 Opus）
    _pricing = {**_fallback_pricing(), **_load_pricing()}
    return _pricing


def calculate_cost(entry: UsageEntry) -> float:
    if entry.cost_usd is not None:
        return entry.cost_usd

    pricing = get_pricing()
    model_key = _resolve_model_key(entry.model, pricing)
    if model_key is None:
        _warn_unknown_model_once(entry.model)
        return 0.0

    info = pricing[model_key]
    if entry.pricing_segments and _segments_cover_entry(entry):
        return sum(
            _calculate_segment(entry.model, model_key, info, segment)
            for segment in entry.pricing_segments
        )

    # Claude / Kimi 的 entry 本身就是单次请求，可直接按 prompt 长度选阶梯价。
    # Codex 是会话累计值；旧日志没有逐轮 segment 时不能拿会话总量冒充单次上下文，只按基础档安全降级。
    prompt_tokens = None if entry.agent_id == "codex" else (
        entry.input_tokens + entry.cache_creation_tokens + entry.cache_read_tokens
    )
    return _calculate_tokens(
        entry.model,
        model_key,
        info,
        entry.timestamp,
        entry.input_tokens,
        entry.output_tokens,
        entry.cache_creation_tokens,
        entry.cache_read_tokens,
        prompt_tokens,
    )


def _segments_cover_entry(entry: UsageEntry) -> bool:
    """只有逐轮数据完整覆盖会话累计值时才采用，旧版/缺字段日志继续走安全降级。"""
    return (
        sum(s.input_tokens for s in entry.pricing_segments) == entry.input_tokens
        and sum(s.output_tokens for s in entry.pricing_segments) == entry.output_tokens
        and sum(s.cache_creation_tokens for s in entry.pricing_segments) == entry.cache_creation_tokens
        and sum(s.cache_read_tokens for s in entry.pricing_segments) == entry.cache_read_tokens
    )


def _calculate_segment(model: str, model_key: str, info: dict, segment: UsageSegment) -> float:
    return _calculate_tokens(
        model,
        model_key,
        info,
        segment.timestamp,
        segment.input_tokens,
        segment.output_tokens,
        segment.cache_creation_tokens,
        segment.cache_read_tokens,
        segment.prompt_tokens,
    )


def _calculate_tokens(
    model: str,
    model_key: str,
    info: dict,
    timestamp: datetime,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    prompt_tokens: int | None,
) -> float:
    if model_key.lower() in _DEEPSEEK_V4_PRICING_KEYS:
        info = _deepseek_pricing(model, timestamp)
    elif model_key in _GEMINI_FLASH_MODELS:
        ts = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
        # 官方公告的时间切价独立于当前缓存，历史请求不会被未来的在线价重算。
        info = _usd(1.5, 7.5, 0.15) if ts >= _GEMINI_FLASH_PRICE_CHANGE_AT else _usd(0.75, 3.75, 0.075)

    info = _select_tiered_pricing(info, prompt_tokens)
    if "tiered_pricing" in info and ("input_cost_per_token" not in info or "output_cost_per_token" not in info):
        _warn_unknown_model_once(model)

    suffix = _context_price_suffix(info, prompt_tokens)
    input_cost = _tier_value(info, "input_cost_per_token", suffix, 0)
    output_cost = _tier_value(info, "output_cost_per_token", suffix, 0)
    cache_creation_cost = _tier_value(
        info, "cache_creation_input_token_cost", suffix, input_cost * 1.25
    )
    cache_read_cost = _tier_value(info, "cache_read_input_token_cost", suffix, input_cost * 0.1)

    return (
        input_tokens * input_cost
        + output_tokens * output_cost
        + cache_creation_tokens * cache_creation_cost
        + cache_read_tokens * cache_read_cost
    )


def _select_tiered_pricing(info: dict, prompt_tokens: int | None) -> dict:
    """LiteLLM 的 range 是 (lower, upper]；无逐请求数据时用最低档，非法档不覆盖基础价。"""
    tiers = info.get("tiered_pricing")
    if not isinstance(tiers, list):
        return info
    valid: list[dict] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        bounds = tier.get("range")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in bounds):
            continue
        lower, upper = bounds
        if not (math.isfinite(lower) and 0 <= lower < upper):
            continue
        rates = [tier.get(key) for key in ("input_cost_per_token", "output_cost_per_token")]
        if not all(
            isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0
            for n in rates
        ):
            continue
        valid.append(tier)
    if not valid:
        return info
    valid.sort(key=lambda tier: tier["range"][0])
    selected = valid[0]
    if prompt_tokens is not None:
        for tier in valid:
            if prompt_tokens > tier["range"][0]:
                selected = tier
    # 返回该档覆盖后的价格，继续兼容已有的 *_above_*k_tokens 格式。
    return {**info, **selected}


def _context_price_suffix(info: dict, prompt_tokens: int | None) -> str:
    if prompt_tokens is None:
        return ""
    thresholds: list[int] = []
    prefix = "input_cost_per_token_above_"
    suffix = "k_tokens"
    for key in info:
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        raw = key[len(prefix):-len(suffix)]
        if raw.isdigit():
            thresholds.append(int(raw))
    inclusive = info.get("long_context_threshold_inclusive", False)
    eligible = [
        threshold
        for threshold in thresholds
        if (prompt_tokens >= threshold * 1000 if inclusive else prompt_tokens > threshold * 1000)
    ]
    return f"_above_{max(eligible)}k_tokens" if eligible else ""


def _tier_value(info: dict, key: str, suffix: str, default: float) -> float:
    if suffix and f"{key}{suffix}" in info:
        return info[f"{key}{suffix}"]
    return info.get(key, default)


def _deepseek_pricing(model: str, timestamp: datetime) -> dict:
    """DeepSeek 官方直营价：新价生效后仅工作日 UTC 两段为峰值，周末全天谷价。"""
    ts = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    ts = ts.astimezone(UTC)
    ml = model.lower().removeprefix("deepseek/")
    pro = ml.startswith("deepseek-v4-pro")
    if ts < _DEEPSEEK_NEW_PRICING_AT:
        info = _cny(3, 6, 0.025) if pro else _cny(1, 2, 0.02)
        info["cache_creation_input_token_cost"] = info["input_cost_per_token"]
        return info

    china_date = ts.astimezone(_CHINA_TZ).date()
    holiday = any(start <= china_date <= end for start, end in _DEEPSEEK_HOLIDAYS)
    peak = not holiday and ts.weekday() < 5 and (1 <= ts.hour < 4 or 6 <= ts.hour < 10)
    if pro:
        info = _cny(9, 27, 0.3) if peak else _cny(4.5, 13.5, 0.15)
    elif ml in _DEEPSEEK_V41_FLASH_MODELS and ts >= _DEEPSEEK_V41_FLASH_AT:
        # V4 Flash 旧 ID 自此路由到 V4.1 Flash；V4 Pro 仍按原价提供。
        info = _cny(2, 8, 0.04) if peak else _cny(1, 4, 0.02)
    else:
        info = _cny(3, 9, 0.1) if peak else _cny(1.5, 4.5, 0.05)
    info["cache_creation_input_token_cost"] = info["input_cost_per_token"]
    return info


def _resolve_model_key(model: str, pricing: dict) -> str | None:
    if not model:
        return None
    # 完整 ID 的专属价始终优先，不能被此前缓存的裸模型兜底遮蔽。
    if model in pricing:
        return model
    if model in _model_key_cache:
        cached = _model_key_cache[model]
        if cached is not None and cached in pricing:
            return cached
    key = _resolve_model_key_uncached(model, pricing)
    _model_key_cache[model] = key
    return key


def _resolve_model_key_uncached(model: str, pricing: dict) -> str | None:
    if model in pricing:
        return model

    ml = model.lower()
    # MiniMax 官方 ID 使用大小写混排；日志小写时也必须命中其精确档位。
    for key in pricing:
        if key.lower() == ml:
            return key
    # model 以 key + "-" 为前缀：处理 dated/variant 后缀（gpt-5-codex-2025-12-01 → gpt-5-codex）
    # "-" 锚点避免 gpt-5 误吞 gpt-5.6-* 这类点号新版本；取最长匹配，避免 gpt-5 误吞 gpt-5-codex-*
    prefix_keys = [
        k for k in pricing
        if (kl := k.lower()) != ml and ml.startswith(kl) and ml[len(kl)] == "-"
    ]
    if prefix_keys:
        return max(prefix_keys, key=len)
    # 反向兜底：key 以 model + "-" 开头（gpt-5 命中 gpt-5-2025-08-07）
    # 加 "-" 锚点避免 gpt-5 撞上 gpt-5-mini
    suffix_keys = [k for k in pricing if k.lower().startswith(ml + "-")]
    if suffix_keys:
        return min(suffix_keys, key=len)

    # 已知 OpenAI 命名空间缺少独立报价时，沿用裸 GPT 模型的精确／日期／系列解析。
    # 只剥一层已知前缀；第三方平台或嵌套 provider 不擅自套用官方价。
    namespace, _, bare_model = ml.partition("/")
    if namespace in ("chatgpt", "openai") and bare_model.startswith("gpt-") and "/" not in bare_model:
        return _resolve_model_key_uncached(bare_model, pricing)

    # 官方 provider 前缀补全：例如 bare `grok-4.6` → LiteLLM 的 `xai/grok-4.6`。
    for model_prefix, providers in _OFFICIAL_PROVIDER_PREFIXES:
        if not ml.startswith(model_prefix):
            continue
        for provider in providers:
            target = provider + ml
            for key in pricing:
                if key.lower() == target:
                    return key

    # 同系列兜底：未知的新 Claude 模型（litellm 收录滞后）退回同系列最新已知价，避免成本静默归零
    for prefix, fallback_key in _FAMILY_FALLBACK:
        if ml.startswith(prefix) and fallback_key in pricing:
            return fallback_key
    return None


def _warn_unknown_model_once(model: str) -> None:
    # 全新系列（非 opus/sonnet/haiku/fable）连系列兜底都接不住，成本会按 $0 计；显形以免静默少算
    if model and model not in _warned_unknown_models:
        _warned_unknown_models.add(model)
        print(
            f"token-tracker: 未知模型 {model!r} 缺少定价，本次成本按 $0 计；"
            "litellm 收录后自动恢复，或在 cost.py 的 _fallback_pricing 补内置价",
            file=sys.stderr,
        )


def _load_pricing() -> dict:
    cached = _read_cache()
    if cached is not None and not _cache_stale():
        return cached

    # 缓存缺失或过期 → 尝试联网刷新；失败时优先用旧缓存（哪怕过期），最后才用内置兜底。
    # 异常集要覆盖整条抓取链：URLError/TimeoutError/ssl.SSLError/OSError（网络与 socket），
    # http.client.HTTPException（IncompleteRead 等截断响应），ValueError（JSON 解析 + decode 失败）。
    # 仍不用裸 except，避免吞掉 AttributeError/KeyError 这类真 bug。
    try:
        return _fetch_and_cache()
    except (URLError, TimeoutError, ssl.SSLError, OSError, http.client.HTTPException, ValueError):
        if cached is not None:
            return cached
        return _fallback_pricing()


def _read_cache() -> dict | None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _cache_stale() -> bool:
    try:
        age = time.time() - CACHE_PATH.stat().st_mtime
    except OSError:
        return True
    return age > CACHE_TTL_SECONDS


def _fetch_and_cache() -> dict:
    try:
        data = _fetch(verify=True)
    except ssl.SSLCertVerificationError:
        # 默认不静默降级 TLS：仅当用户显式 TT_PRICING_INSECURE_TLS=1 时才放行（抓的是公开定价表）
        if os.environ.get(_INSECURE_TLS_ENV) != "1":
            raise
        _warn_insecure_once()
        data = _fetch(verify=False)

    _write_cache(data)
    return data


def _fetch(verify: bool) -> dict:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "token-tracker/0.1"})
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        return json.loads(resp.read().decode())


def _write_cache(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _warn_insecure_once() -> None:
    global _warned_insecure
    if not _warned_insecure:
        print(
            f"token-tracker: 已按 {_INSECURE_TLS_ENV}=1 关闭 TLS 证书校验（仅用于抓取公开定价表）",
            file=sys.stderr,
        )
        _warned_insecure = True


# Anthropic 官方价（claude.com/pricing 2026-06 核对）。opus 4.5/4.6/4.7/4.8 同价。
_OPUS_PRICING = {
    "input_cost_per_token": 5e-6,
    "output_cost_per_token": 25e-6,
    "cache_creation_input_token_cost": 6.25e-6,
    "cache_read_input_token_cost": 0.5e-6,
}

# Fable 5 / Mythos 5 同价，是 Opus 档的 2 倍（$10/$50 每百万 token）
_FABLE_PRICING = {
    "input_cost_per_token": 10e-6,
    "output_cost_per_token": 50e-6,
    "cache_creation_input_token_cost": 12.5e-6,
    "cache_read_input_token_cost": 1.0e-6,
}

# 国产模型多以人民币计价，统一折 USD 入表，与 CC/Codex 同口径（2026-06 近似汇率）
_CNY_PER_USD = 7.1


def _cny(input_m: float, output_m: float, cache_read_m: float | None = None) -> dict:
    """人民币「元 / 百万 tokens」→ USD per token（÷汇率 ÷1e6）。国产模型按中国站人民币价折算。"""
    info = {
        "input_cost_per_token": input_m / _CNY_PER_USD * 1e-6,
        "output_cost_per_token": output_m / _CNY_PER_USD * 1e-6,
    }
    if cache_read_m is not None:
        info["cache_read_input_token_cost"] = cache_read_m / _CNY_PER_USD * 1e-6
    return info


def _usd(input_m: float, output_m: float, cache_read_m: float | None = None) -> dict:
    """美元「$ / 百万 tokens」→ USD per token（÷1e6）。用于只有官方国际站 USD 价的模型。"""
    info = {
        "input_cost_per_token": input_m * 1e-6,
        "output_cost_per_token": output_m * 1e-6,
    }
    if cache_read_m is not None:
        info["cache_read_input_token_cost"] = cache_read_m * 1e-6
    return info


def _fallback_pricing() -> dict:
    return {
        # https://platform.claude.com/docs/en/models/fable-5-1/overview
        # Fable 5.1 仅缓存读取降至 $0.25/MTok；旧 Fable 5 / Mythos 5 保留原价。
        "claude-fable-5-1": {**_FABLE_PRICING, "cache_read_input_token_cost": 0.25e-6},
        "claude-fable-5": _FABLE_PRICING,
        "claude-mythos-5-1": {**_FABLE_PRICING, "cache_read_input_token_cost": 0.25e-6},
        "claude-mythos-5": _FABLE_PRICING,
        # https://platform.claude.com/docs/en/models/opus-5-5/overview
        # Opus 5.5 降价；Opus 5 精确 key 保留历史价，不受系列兜底更新影响。
        "claude-opus-5-5": {
            "input_cost_per_token": 4e-6,
            "output_cost_per_token": 20e-6,
            "cache_creation_input_token_cost": 5e-6,
            "cache_read_input_token_cost": 0.2e-6,
        },
        "claude-opus-5": _OPUS_PRICING,
        "claude-opus-4-8": _OPUS_PRICING,
        "claude-opus-4-7": _OPUS_PRICING,
        "claude-opus-4-6": _OPUS_PRICING,
        "claude-opus-4-5": _OPUS_PRICING,
        # https://platform.claude.com/docs/en/about-claude/pricing (2026-10-10)
        "claude-sonnet-5-5": {
            **_usd(2, 10, 0.1),
            "cache_creation_input_token_cost": 2.5e-6,
        },
        "claude-haiku-5-5": {
            **_usd(0.1, 0.5, 0.01),
            "cache_creation_input_token_cost": 0.125e-6,
            "input_cost_per_token_above_100k_tokens": 0.5e-6,
            "output_cost_per_token_above_100k_tokens": 2.5e-6,
            "cache_creation_input_token_cost_above_100k_tokens": 0.625e-6,
            "cache_read_input_token_cost_above_100k_tokens": 0.05e-6,
        },
        # Sonnet 5 的 $2/$10 导入价已于 2026-08-10 由 Anthropic 宣布转为永久价。
        "claude-sonnet-5": {
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 10e-6,
            "cache_creation_input_token_cost": 2.5e-6,
            "cache_read_input_token_cost": 0.2e-6,
        },
        "claude-sonnet-4-6": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 15e-6,
            "cache_creation_input_token_cost": 3.75e-6,
            "cache_read_input_token_cost": 0.3e-6,
        },
        "claude-haiku-4-5-20251001": {
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 5e-6,
            "cache_creation_input_token_cost": 1.25e-6,
            "cache_read_input_token_cost": 0.1e-6,
        },
        # https://developers.openai.com/api/docs/models/gpt-6.1-sol
        "gpt-6.1-sol": {
            **_usd(2, 10, 0.1),
            "cache_creation_input_token_cost": 2.5e-6,
            "input_cost_per_token_above_272k_tokens": 4e-6,
            "output_cost_per_token_above_272k_tokens": 15e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 5e-6,
            "cache_read_input_token_cost_above_272k_tokens": 0.2e-6,
        },
        # https://developers.openai.com/api/docs/models/gpt-6-astra
        # Standard：超过 272K 输入，整次请求 input/cache 2x、output 1.5x。
        "gpt-6-astra": {
            "input_cost_per_token": 10e-6,
            "output_cost_per_token": 50e-6,
            "cache_creation_input_token_cost": 12.5e-6,
            "cache_read_input_token_cost": 1e-6,
            "input_cost_per_token_above_272k_tokens": 20e-6,
            "output_cost_per_token_above_272k_tokens": 75e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 25e-6,
            "cache_read_input_token_cost_above_272k_tokens": 2e-6,
        },
        # https://developers.openai.com/api/docs/models/gpt-6-sol
        "gpt-6-sol": {
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 10e-6,
            "cache_creation_input_token_cost": 2.5e-6,
            "cache_read_input_token_cost": 0.2e-6,
            "input_cost_per_token_above_272k_tokens": 4e-6,
            "output_cost_per_token_above_272k_tokens": 15e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 5e-6,
            "cache_read_input_token_cost_above_272k_tokens": 0.4e-6,
        },
        # https://developers.openai.com/api/docs/models/gpt-6-luna
        "gpt-6-luna": {
            "input_cost_per_token": 0.1e-6,
            "output_cost_per_token": 0.5e-6,
            "cache_creation_input_token_cost": 0.125e-6,
            "cache_read_input_token_cost": 0.01e-6,
            "input_cost_per_token_above_272k_tokens": 0.2e-6,
            "output_cost_per_token_above_272k_tokens": 0.75e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 0.25e-6,
            "cache_read_input_token_cost_above_272k_tokens": 0.02e-6,
        },
        "gpt-5": {
            "input_cost_per_token": 1.25e-6,
            "output_cost_per_token": 10e-6,
            "cache_read_input_token_cost": 0.125e-6,
        },
        "gpt-5.5": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 30e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        # GPT-5.6 系列：短上下文基础档；超过 272K 输入后整次请求按 2x input / 1.5x output。
        # Sol 当前 $4/$20 为至少持续到 2026-11-21 的促销价。
        "gpt-5.6-sol": {
            "input_cost_per_token": 4e-6,
            "output_cost_per_token": 20e-6,
            "cache_creation_input_token_cost": 5e-6,
            "cache_read_input_token_cost": 0.4e-6,
            "input_cost_per_token_above_272k_tokens": 8e-6,
            "output_cost_per_token_above_272k_tokens": 30e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 10e-6,
            "cache_read_input_token_cost_above_272k_tokens": 0.8e-6,
        },
        "gpt-5.6-terra": {
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 12e-6,
            "cache_creation_input_token_cost": 2.5e-6,
            "cache_read_input_token_cost": 0.2e-6,
            "input_cost_per_token_above_272k_tokens": 4e-6,
            "output_cost_per_token_above_272k_tokens": 18e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 5e-6,
            "cache_read_input_token_cost_above_272k_tokens": 0.4e-6,
        },
        "gpt-5.6-luna": {
            "input_cost_per_token": 0.2e-6,
            "output_cost_per_token": 1.2e-6,
            "cache_creation_input_token_cost": 0.25e-6,
            "cache_read_input_token_cost": 0.02e-6,
            "input_cost_per_token_above_272k_tokens": 0.4e-6,
            "output_cost_per_token_above_272k_tokens": 1.8e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 0.5e-6,
            "cache_read_input_token_cost_above_272k_tokens": 0.04e-6,
        },
        "gpt-5-codex": {
            "input_cost_per_token": 1.25e-6,
            "output_cost_per_token": 10e-6,
            "cache_read_input_token_cost": 0.125e-6,
        },
        "gpt-5-mini": {
            "input_cost_per_token": 0.25e-6,
            "output_cost_per_token": 2e-6,
            "cache_read_input_token_cost": 0.025e-6,
        },
        "gpt-5-nano": {
            "input_cost_per_token": 0.05e-6,
            "output_cost_per_token": 0.4e-6,
            "cache_read_input_token_cost": 0.005e-6,
        },
        "gpt-5-pro": {
            "input_cost_per_token": 15e-6,
            "output_cost_per_token": 120e-6,
        },
        "codex-mini-latest": {
            "input_cost_per_token": 1.5e-6,
            "output_cost_per_token": 6e-6,
            "cache_read_input_token_cost": 0.375e-6,
        },
        # ---- 国产模型（2026-06 官方核实）。除 GLM 用 z.ai 国际站 USD 外，其余按各家中国站
        # 人民币价 ÷7.1 折算；阶梯定价模型（Qwen3-Coder / Doubao）统一取 0-32K 基础档。----
        # Kimi / Moonshot（platform.kimi.com 官方人民币价；老 kimi-k2-instruct 已 EOL，靠系列兜底）
        # kimi-k3 用官方国际站 USD 价（2026-07-16 上线，$3/$15，cached input $0.30，1M 上下文不分档）；
        # Kimi Code 会话 wire 里的 "kimi-code/k3" id 由 _FAMILY_FALLBACK 路由到此 key
        "kimi-k3": _usd(3, 15, 0.3),
        "kimi-k2.7-code": _cny(6.5, 27, 1.3),
        "kimi-k2.6": _cny(6.5, 27, 1.1),
        "kimi-k2.5": _cny(4, 21, 0.7),
        "moonshot-v1-8k": _cny(2, 10),
        "moonshot-v1-32k": _cny(5, 20),
        "moonshot-v1-128k": _cny(10, 30),
        # 智谱 GLM（z.ai 国际站官方 USD；中国站按量完整价含缓存无法从官方 SPA 取得，国内口径可能偏高）
        "glm-4.6": _usd(0.6, 2.2, 0.11),
        "glm-4.5": _usd(0.6, 2.2, 0.11),
        "glm-4.5-air": _usd(0.2, 1.1, 0.03),
        "glm-4.7": _usd(0.6, 2.2, 0.11),
        "glm-5": _usd(1.0, 3.2, 0.2),
        "glm-5.1": _usd(1.4, 4.4, 0.26),
        # https://docs.z.ai/guides/overview/pricing
        "glm-5.2": _usd(1.4, 4.4, 0.26),
        "glm-5.3": _usd(1.4, 4.4, 0.26),
        "glm-5.3-flash": _usd(0.15, 0.5, 0.03),
        "glm-5.3-flashx": _usd(0.37, 1.25, 0.075),
        # 阿里 Qwen（中国站百炼人民币价，0-32K 基础档）
        # https://help.aliyun.com/zh/model-studio/model-pricing
        # 新 Plus 隐式缓存按输入价的 20% 估算；显式缓存模式未在日志中区分。
        "qwen3.7-plus": {
            **_cny(1.6, 6.4, 0.32),  # 裸 alias 当前限时 8 折；日期版保留原价。
            "input_cost_per_token_above_256k_tokens": 4.8 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_256k_tokens": 19.2 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_256k_tokens": 0.96 / _CNY_PER_USD * 1e-6,
        },
        "qwen3.7-plus-2026-05-26": {
            **_cny(2, 8, 0.4),
            "input_cost_per_token_above_256k_tokens": 6 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_256k_tokens": 24 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_256k_tokens": 1.2 / _CNY_PER_USD * 1e-6,
        },
        "qwen3.6-plus": {
            **_cny(2, 12, 0.4),
            "input_cost_per_token_above_256k_tokens": 8 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_256k_tokens": 48 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_256k_tokens": 1.6 / _CNY_PER_USD * 1e-6,
        },
        "qwen3-coder-plus": {
            **_cny(4, 16, 0.4),
            "input_cost_per_token_above_32k_tokens": 6 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_32k_tokens": 24 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_32k_tokens": 0.6 / _CNY_PER_USD * 1e-6,
            "input_cost_per_token_above_128k_tokens": 10 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_128k_tokens": 40 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_128k_tokens": 1 / _CNY_PER_USD * 1e-6,
            "input_cost_per_token_above_256k_tokens": 20 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_256k_tokens": 200 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_256k_tokens": 2 / _CNY_PER_USD * 1e-6,
        },
        "qwen3-coder-flash": {
            **_cny(1, 4, 0.2),
            "input_cost_per_token_above_32k_tokens": 1.5 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_32k_tokens": 6 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_32k_tokens": 0.3 / _CNY_PER_USD * 1e-6,
            "input_cost_per_token_above_128k_tokens": 2.5 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_128k_tokens": 10 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_128k_tokens": 0.5 / _CNY_PER_USD * 1e-6,
            "input_cost_per_token_above_256k_tokens": 5 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_256k_tokens": 25 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_256k_tokens": 1 / _CNY_PER_USD * 1e-6,
        },
        "qwen3-coder-next": {
            **_cny(1, 4),
            "input_cost_per_token_above_32k_tokens": 1.5 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_32k_tokens": 6 / _CNY_PER_USD * 1e-6,
            "input_cost_per_token_above_128k_tokens": 2.5 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_128k_tokens": 10 / _CNY_PER_USD * 1e-6,
        },
        "qwen-max": _cny(2.5, 10),
        "qwen-plus": _cny(0.8, 2),
        # 火山方舟 Doubao（中国站人民币价，0-32K 基础档）
        "doubao-seed-1-6": _cny(0.8, 8),
        "doubao-seed-code": _cny(1.2, 8),
        "doubao-seed-2.0-code": {
            **_cny(3.2, 16, 0.64),
            "input_cost_per_token_above_32k_tokens": 4.8 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_32k_tokens": 24 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_32k_tokens": 0.96 / _CNY_PER_USD * 1e-6,
            "input_cost_per_token_above_128k_tokens": 9.6 / _CNY_PER_USD * 1e-6,
            "output_cost_per_token_above_128k_tokens": 48 / _CNY_PER_USD * 1e-6,
            "cache_read_input_token_cost_above_128k_tokens": 1.92 / _CNY_PER_USD * 1e-6,
        },
        "doubao-seed-2.1-pro": _cny(6, 30, 1.2),
        "doubao-1-5-pro-32k": _cny(0.8, 2, 0.16),
        "doubao-1-5-pro-256k": _cny(5, 9),
        # DeepSeek：表内放当前高峰档作静态元数据；calculate_cost 按生效时间、工作日与峰谷时段动态覆盖。
        # 周一至周五北京时间 9:00-12:00 / 14:00-18:00 为高峰，周末全天与其余时段均为谷价。
        # https://api-docs.deepseek.com/quick_start/pricing/
        "deepseek-flash": _cny(2, 8, 0.04),
        "deepseek-v4-flash": _cny(3, 9, 0.1),
        "deepseek-v4-pro": _cny(9, 27, 0.3),
        "deepseek-v4-flash-vision-exp": _cny(3, 9, 0.1),
        "deepseek-chat": _cny(3, 9, 0.1),
        "deepseek-reasoner": _cny(3, 9, 0.1),
        # MiniMax（官方 USD，与中国站÷7 自洽；M2/M2.1/M2.5 同价 legacy）
        "MiniMax-M2": _usd(0.3, 1.2, 0.03),
        "MiniMax-M2.1": _usd(0.3, 1.2, 0.03),
        "MiniMax-M2.1-highspeed": {**_usd(0.6, 2.4, 0.03), "cache_creation_input_token_cost": 0.375e-6},
        "MiniMax-M2.5": _usd(0.3, 1.2, 0.03),
        "MiniMax-M2.5-highspeed": {**_usd(0.6, 2.4, 0.03), "cache_creation_input_token_cost": 0.375e-6},
        "MiniMax-M2.7": _usd(0.3, 1.2, 0.06),
        "MiniMax-M2.7-highspeed": {**_usd(0.6, 2.4, 0.06), "cache_creation_input_token_cost": 0.375e-6},
        # https://platform.minimax.io/docs/pricing/overview
        "MiniMax-M3": {
            **_usd(0.3, 1.2, 0.06),
            "input_cost_per_token_above_512k_tokens": 0.6e-6,
            "output_cost_per_token_above_512k_tokens": 2.4e-6,
            "cache_read_input_token_cost_above_512k_tokens": 0.12e-6,
        },
        # 小米 MiMo（mimo.mi.com 官方中国站人民币价；与 DeepSeek 同价，V2.5-Pro 主攻 agentic 编程）
        "mimo-v2.5-pro": _cny(3, 6, 0.025),
        "mimo-v2.5": _cny(1, 2, 0.02),
        # https://mimo.mi.com/models/en-US/mimo-v2.6-pro
        "mimo-v2.6-pro": _cny(3, 6, 0.025),
        "mimo-v2.6-flash": _cny(1, 2, 0.02),
        # https://ai.google.dev/gemini-api/docs/pricing
        # 当前生效价；2027-01-01 翻倍按请求时间在 _calculate_tokens 中处理。
        "gemini-3.6-flash": _usd(0.75, 3.75, 0.075),
        "gemini-3.8-flash": _usd(0.75, 3.75, 0.075),
        # xAI Grok（docs.x.ai 官方 USD）。2026-05-15 退役潮：grok-4-fast/4.1-fast/grok-3 路由到 grok-4.3，
        # grok-code-fast-1 退役为 grok-build-0.1 别名（退役 slug 靠 _FAMILY_FALLBACK 接住）。
        "grok-4.3": {
            **_usd(1.25, 2.5, 0.2),
            "long_context_threshold_inclusive": True,
            "input_cost_per_token_above_200k_tokens": 2.5e-6,
            "output_cost_per_token_above_200k_tokens": 5e-6,
            "cache_read_input_token_cost_above_200k_tokens": 0.4e-6,
        },
        "grok-build-0.1": {
            **_usd(1.0, 2.0, 0.2),
            "long_context_threshold_inclusive": True,
            "input_cost_per_token_above_200k_tokens": 2e-6,
            "output_cost_per_token_above_200k_tokens": 4e-6,
            "cache_read_input_token_cost_above_200k_tokens": 0.4e-6,
        },
        "grok-code-fast-1": {
            **_usd(1.0, 2.0, 0.2),
            "long_context_threshold_inclusive": True,
            "input_cost_per_token_above_200k_tokens": 2e-6,
            "output_cost_per_token_above_200k_tokens": 4e-6,
            "cache_read_input_token_cost_above_200k_tokens": 0.4e-6,
        },
        "grok-4.5": {
            **_usd(2.0, 6.0, 0.3),
            "long_context_threshold_inclusive": True,
            "input_cost_per_token_above_200k_tokens": 4e-6,
            "output_cost_per_token_above_200k_tokens": 12e-6,
            "cache_read_input_token_cost_above_200k_tokens": 0.6e-6,
        },
        "grok-4.6": {
            **_usd(2.0, 6.0, 0.5),
            "long_context_threshold_inclusive": True,
            "input_cost_per_token_above_200k_tokens": 4e-6,
            "output_cost_per_token_above_200k_tokens": 12e-6,
            "cache_read_input_token_cost_above_200k_tokens": 1e-6,
        },
        # https://docs.x.ai/developers/models
        "grok-4.7": {
            **_usd(2.0, 6.0, 0.5),
            "long_context_threshold_inclusive": True,
            "input_cost_per_token_above_200k_tokens": 4e-6,
            "output_cost_per_token_above_200k_tokens": 12e-6,
            "cache_read_input_token_cost_above_200k_tokens": 1e-6,
        },
    }
