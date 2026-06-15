import json
import subprocess
import time
from pathlib import Path
from token_tracker.adapters.providers.base import (
    RateProvider,
    ProviderRateData,
    register_provider,
    create_provider,
    get_cached_data,
    set_cached_data,
    get_cache_dir,
)
from token_tracker.adapters.providers.script import ScriptProvider


class SampleProvider(RateProvider):
    def __init__(self, value):
        self.value = value

    def get_limits(self):
        return ProviderRateData(five_hour_pct=self.value, source="test")

    @classmethod
    def from_config(cls, config):
        return cls(config.get("value", 0))


def test_provider_registry():
    register_provider("test")(SampleProvider)
    provider = create_provider({"type": "test", "value": 50.0})
    assert provider is not None
    result = provider.get_limits()
    assert result.five_hour_pct == 50.0
    assert result.source == "test"


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("token_tracker.adapters.providers.base.get_cache_dir", lambda: tmp_path)
    set_cached_data("test_key", {"five_hour_pct": 75.0})
    cached = get_cached_data("test_key", 60)
    assert cached == {"five_hour_pct": 75.0}


def test_cache_expired(tmp_path, monkeypatch):
    monkeypatch.setattr("token_tracker.adapters.providers.base.get_cache_dir", lambda: tmp_path)
    set_cached_data("expired", {"data": "old"})
    # 模拟时间流逝
    import token_tracker.adapters.providers.base as base_mod
    original_time = time.time
    monkeypatch.setattr(time, "time", lambda: original_time() + 120)
    cached = get_cached_data("expired", 60)
    assert cached is None


def test_script_provider_success(tmp_path):
    script = tmp_path / "test_script.py"
    script.write_text('''
import json
print(json.dumps({
    "five_hour": {"used_percentage": 35.2, "resets_at": 1718456789},
    "seven_day": {"used_percentage": 12.5},
    "source": "test-script"
}))
''', encoding="utf-8")

    provider = ScriptProvider(command=f"python {script}", cache_ttl=60)
    result = provider.get_limits()
    assert result.five_hour_pct == 35.2
    assert result.five_hour_resets_at == 1718456789
    assert result.seven_day_pct == 12.5
    assert result.seven_day_resets_at is None
    assert result.source == "test-script"


def test_script_provider_timeout(monkeypatch):
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="test", timeout=1)
    monkeypatch.setattr(subprocess, "run", mock_run)
    provider = ScriptProvider(command="sleep 5", cache_ttl=60)
    result = provider.get_limits()
    assert result is None


def test_script_provider_invalid_json(tmp_path):
    script = tmp_path / "bad_script.py"
    script.write_text('print("not json")', encoding="utf-8")
    provider = ScriptProvider(command=f"python {script}", cache_ttl=60)
    result = provider.get_limits()
    assert result is None
