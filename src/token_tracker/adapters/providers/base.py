import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProviderRateData:
    """统一的配额数据格式，所有提供者都输出此格式"""
    five_hour_pct: float | None = None
    five_hour_resets_at: int | None = None
    seven_day_pct: float | None = None
    seven_day_resets_at: int | None = None
    monthly_pct: float | None = None
    monthly_resets_at: int | None = None
    source: str = ""


class RateProvider(ABC):
    """配额提供者抽象基类"""

    @abstractmethod
    def get_limits(self) -> ProviderRateData | None:
        pass

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict) -> "RateProvider":
        pass

    def _cache_key(self) -> str:
        config_str = json.dumps(self.__dict__, sort_keys=True)
        return f"{self.__class__.__name__}:{hashlib.md5(config_str.encode()).hexdigest()[:8]}"


_provider_registry: dict[str, type[RateProvider]] = {}


def register_provider(type_name: str):
    """提供者注册装饰器"""
    def decorator(cls: type[RateProvider]) -> type[RateProvider]:
        _provider_registry[type_name] = cls
        return cls
    return decorator


def create_provider(config: dict) -> RateProvider | None:
    """根据配置创建提供者实例"""
    provider_type = config.get("type")
    if not provider_type or provider_type not in _provider_registry:
        return None
    return _provider_registry[provider_type].from_config(config)


def get_cache_dir() -> Path:
    """获取缓存目录"""
    return Path.home() / ".cache" / "token-tracker"


def get_cached_data(cache_key: str, ttl_seconds: int) -> dict | None:
    """读取缓存数据"""
    cache_file = get_cache_dir() / f"quota_{cache_key}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("_timestamp", 0) < ttl_seconds:
            return data.get("payload")
    except (json.JSONDecodeError, OSError):
        pass
    return None


def set_cached_data(cache_key: str, payload: dict) -> None:
    """写入缓存数据"""
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"quota_{cache_key}.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"_timestamp": time.time(), "payload": payload}, f)
    except OSError:
        pass
