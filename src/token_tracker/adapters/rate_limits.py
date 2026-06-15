import json
import os
from datetime import UTC, datetime

from .types import RateLimits, normalize_pct
from .providers import create_provider

STATUS_FILE = os.path.expanduser("~/.claude/tt-status.json")
CONFIG_FILE = os.path.expanduser("~/.claude/tt-config.json")


def _load_official() -> RateLimits | None:
    """从 tt-status.json 读取官方注入的配额数据（原有逻辑保留）"""
    if not os.path.exists(STATUS_FILE):
        return None

    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    rl = data.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}

    now_ts = datetime.now(UTC).timestamp()
    five_reset = five.get("resets_at")
    five_pct = normalize_pct(five.get("used_percentage"), five_reset, now_ts)

    seven_reset = seven.get("resets_at")
    seven_pct = normalize_pct(seven.get("used_percentage"), seven_reset, now_ts)

    model_info = data.get("model") or {}
    model_name = model_info.get("display_name") or model_info.get("id") or ""

    if five_pct is None and seven_pct is None and not model_name:
        return None

    return RateLimits(
        five_hour_pct=five_pct,
        five_hour_resets_at=five_reset,
        seven_day_pct=seven_pct,
        seven_day_resets_at=seven_reset,
        model=model_name,
    )


def _load_config() -> dict | None:
    """读取用户配置文件"""
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _from_provider_data(data) -> RateLimits:
    """将 ProviderRateData 转换为 RateLimits"""
    now_ts = datetime.now(UTC).timestamp()
    return RateLimits(
        five_hour_pct=normalize_pct(data.five_hour_pct, data.five_hour_resets_at, now_ts),
        five_hour_resets_at=data.five_hour_resets_at,
        seven_day_pct=normalize_pct(data.seven_day_pct, data.seven_day_resets_at, now_ts),
        seven_day_resets_at=data.seven_day_resets_at,
        monthly_pct=normalize_pct(data.monthly_pct, data.monthly_resets_at, now_ts),
        monthly_resets_at=data.monthly_resets_at,
        model=data.source or "",
    )


def load_rate_limits() -> RateLimits | None:
    """链式查询配额：官方注入 → 配置的提供者 → None（降级）"""
    # 1. 优先官方数据（如果有有效配额）
    official = _load_official()
    if official and (official.five_hour_pct is not None or official.seven_day_pct is not None):
        return official

    # 2. 尝试第三方提供者
    config = _load_config()
    if config and "rate_provider" in config:
        provider = create_provider(config["rate_provider"])
        if provider:
            data = provider.get_limits()
            if data and (data.five_hour_pct is not None or data.seven_day_pct is not None or data.monthly_pct is not None):
                # 保留官方 model 信息，叠加 provider source
                result = _from_provider_data(data)
                if official and official.model:
                    result.model = official.model
                return result

    # 3. 降级：返回官方数据（即使没有配额，至少有 model 信息）
    return official
