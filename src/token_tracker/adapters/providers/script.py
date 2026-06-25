import json
import subprocess
import sys

from .base import (
    ProviderRateData,
    RateProvider,
    get_cached_data,
    register_provider,
    set_cached_data,
)


@register_provider("script")
class ScriptProvider(RateProvider):
    """执行外部脚本获取配额数据

    脚本需要输出标准 JSON 格式：
    {
        "five_hour": {"used_percentage": 35.2, "resets_at": 1718456789},
        "seven_day": {"used_percentage": 12.5, "resets_at": 1718976000},
        "monthly": {"used_percentage": 45.0, "resets_at": 1719784800},
        "source": "火山方舟"
    }
    """

    def __init__(self, command: str, cache_ttl: int = 60, timeout: int = 10):
        self.command = command
        self.cache_ttl = cache_ttl
        self.timeout = timeout

    @classmethod
    def from_config(cls, config: dict) -> "ScriptProvider":
        return cls(
            command=config.get("command", ""),
            cache_ttl=int(config.get("cache_ttl", 60)),
            timeout=int(config.get("timeout", 10)),
        )

    def get_limits(self) -> ProviderRateData | None:
        if not self.command:
            return None

        cache_key = self._cache_key()
        cached = get_cached_data(cache_key, self.cache_ttl)
        if cached:
            return self._parse_result(cached)

        try:
            result = subprocess.run(
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode != 0:
                print(f"ScriptProvider: command failed with code {result.returncode}", file=sys.stderr)
                if result.stderr:
                    print(f"stderr: {result.stderr[:200]}", file=sys.stderr)
                return None

            data = json.loads(result.stdout.strip())
            set_cached_data(cache_key, data)
            return self._parse_result(data)
        except subprocess.TimeoutExpired:
            print(f"ScriptProvider: command timed out after {self.timeout}s", file=sys.stderr)
            return None
        except json.JSONDecodeError as e:
            print(f"ScriptProvider: invalid JSON output: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"ScriptProvider: unexpected error: {e}", file=sys.stderr)
            return None

    def _parse_result(self, data: dict) -> ProviderRateData:
        five_hour = data.get("five_hour") or {}
        seven_day = data.get("seven_day") or {}
        monthly = data.get("monthly") or {}

        return ProviderRateData(
            five_hour_pct=five_hour.get("used_percentage"),
            five_hour_resets_at=five_hour.get("resets_at"),
            seven_day_pct=seven_day.get("used_percentage"),
            seven_day_resets_at=seven_day.get("resets_at"),
            monthly_pct=monthly.get("used_percentage"),
            monthly_resets_at=monthly.get("resets_at"),
            source=data.get("source", "script"),
        )
