# 预导入所有 provider 模块，触发 @register_provider 装饰器执行
from . import script
from .base import (
    ProviderRateData,
    RateProvider,
    create_provider,
    get_cached_data,
    register_provider,
    set_cached_data,
)

__all__ = [
    "RateProvider",
    "ProviderRateData",
    "register_provider",
    "create_provider",
    "get_cached_data",
    "set_cached_data",
    "script",
]
