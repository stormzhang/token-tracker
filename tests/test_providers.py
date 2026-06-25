import json
import subprocess
import time

from token_tracker.adapters.providers.base import (
    ProviderRateData,
    RateProvider,
    create_provider,
    get_cached_data,
    register_provider,
    set_cached_data,
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
    original_time = time.time
    monkeypatch.setattr(time, "time", lambda: original_time() + 120)
    cached = get_cached_data("expired", 60)
    assert cached is None


def test_cache_missing_dir(tmp_path):
    cache_dir = tmp_path / "nonexistent" / "cache"
    set_cached_data("new_key", {"val": 1})
    assert not cache_dir.exists()


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


def test_script_provider_partial(tmp_path):
    script = tmp_path / "partial.py"
    script.write_text('''
import json
print(json.dumps({"five_hour": {"used_percentage": 50.0}, "source": "partial"}))
''', encoding="utf-8")
    provider = ScriptProvider(command=f"python {script}", cache_ttl=60)
    result = provider.get_limits()
    assert result.five_hour_pct == 50.0
    assert result.seven_day_pct is None
    assert result.monthly_pct is None
    assert result.source == "partial"


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


def test_script_provider_nonzero_exit(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text('import sys; sys.exit(1)', encoding="utf-8")
    provider = ScriptProvider(command=f"python {script}", cache_ttl=60)
    result = provider.get_limits()
    assert result is None


def test_script_provider_empty_command():
    provider = ScriptProvider(command="", cache_ttl=60)
    result = provider.get_limits()
    assert result is None


def test_script_provider_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setattr("token_tracker.adapters.providers.base.get_cache_dir", lambda: tmp_path)
    provider = ScriptProvider(command="echo '{}'", cache_ttl=60)
    cache_key = provider._cache_key()
    set_cached_data(cache_key, {"five_hour": {"used_percentage": 99.0}})
    result = provider.get_limits()
    assert result.five_hour_pct == 99.0


def test_create_provider_unknown_type():
    result = create_provider({"type": "nonexistent"})
    assert result is None


def test_provider_rate_data_defaults():
    data = ProviderRateData()
    assert data.five_hour_pct is None
    assert data.seven_day_pct is None
    assert data.monthly_pct is None
    assert data.source == ""


def test_rate_limits_monthly_fields():
    from token_tracker.adapters.types import RateLimits
    rl = RateLimits(monthly_pct=50.0, monthly_resets_at=1719784800)
    assert rl.monthly_pct == 50.0
    assert rl.monthly_resets_at == 1719784800
    assert rl.five_hour_pct is None
    assert rl.seven_day_pct is None


def test_from_provider_data_normalize():
    from token_tracker.adapters.rate_limits import _from_provider_data
    data = ProviderRateData(
        five_hour_pct=80.0,
        five_hour_resets_at=1,
        source="test",
    )
    result = _from_provider_data(data)
    assert result.five_hour_pct == 0.0


def test_load_rate_limits_official_wins(tmp_path, monkeypatch):
    status_file = tmp_path / "tt-status.json"
    status_file.write_text(json.dumps({
        "rate_limits": {
            "five_hour": {"used_percentage": 30.0, "resets_at": 9999999999}
        },
        "model": {"display_name": "claude-opus-4"}
    }), encoding="utf-8")
    monkeypatch.setattr("token_tracker.adapters.rate_limits.STATUS_FILE", str(status_file))
    monkeypatch.setattr("token_tracker.adapters.rate_limits.CONFIG_FILE", str(tmp_path / "nonexistent.json"))

    from token_tracker.adapters.rate_limits import load_rate_limits
    result = load_rate_limits()
    assert result.five_hour_pct == 30.0
    assert result.model == "claude-opus-4"


def test_load_rate_limits_provider_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("token_tracker.adapters.rate_limits.STATUS_FILE", str(tmp_path / "nonexistent.json"))
    config_file = tmp_path / "tt-config.json"
    config_file.write_text(json.dumps({
        "rate_provider": {"type": "test", "value": 45.0}
    }), encoding="utf-8")
    monkeypatch.setattr("token_tracker.adapters.rate_limits.CONFIG_FILE", str(config_file))

    from token_tracker.adapters.providers.base import _provider_registry
    _provider_registry["test"] = SampleProvider

    from token_tracker.adapters.rate_limits import load_rate_limits
    result = load_rate_limits()
    assert result is not None
    assert result.five_hour_pct == 45.0
    assert result.model == "test"


def test_load_rate_limits_model_overlay(tmp_path, monkeypatch):
    status_file = tmp_path / "tt-status.json"
    status_file.write_text(json.dumps({
        "model": {"display_name": "official-model"}
    }), encoding="utf-8")
    monkeypatch.setattr("token_tracker.adapters.rate_limits.STATUS_FILE", str(status_file))
    config_file = tmp_path / "tt-config.json"
    config_file.write_text(json.dumps({
        "rate_provider": {"type": "test-overlay", "value": 60.0}
    }), encoding="utf-8")
    monkeypatch.setattr("token_tracker.adapters.rate_limits.CONFIG_FILE", str(config_file))

    class OverlayProvider(RateProvider):
        def get_limits(self):
            return ProviderRateData(five_hour_pct=60.0, source="overlay")
        @classmethod
        def from_config(cls, config):
            return cls()
    from token_tracker.adapters.providers.base import _provider_registry
    _provider_registry["test-overlay"] = OverlayProvider

    from token_tracker.adapters.rate_limits import load_rate_limits
    result = load_rate_limits()
    assert result.five_hour_pct == 60.0
    assert result.model == "official-model"


def test_load_rate_limits_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr("token_tracker.adapters.rate_limits.STATUS_FILE", str(tmp_path / "nonexistent.json"))
    monkeypatch.setattr("token_tracker.adapters.rate_limits.CONFIG_FILE", str(tmp_path / "nonexistent.json"))

    from token_tracker.adapters.rate_limits import load_rate_limits
    result = load_rate_limits()
    assert result is None
