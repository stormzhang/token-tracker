"""2026-10 官方价格校准：离线底座、单次请求阶梯、在线表结构和历史价隔离。"""

from datetime import UTC, datetime, timedelta, timezone
from urllib.error import URLError

import pytest

from token_tracker.adapters.types import UsageEntry, UsageSegment
from token_tracker.analyzer import cost
from token_tracker.ui.format import MODEL_SHORT


def entry(model, **kwargs):
    values = dict(
        timestamp=datetime(2026, 10, 9, tzinfo=UTC), session_id="s", message_id="m",
        request_id="r", model=model, input_tokens=0, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0, cost_usd=None, project="p", agent_id="claude",
    )
    values.update(kwargs)
    return UsageEntry(**values)


@pytest.fixture(autouse=True)
def builtin_prices(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    monkeypatch.setattr(cost, "_model_key_cache", {})


@pytest.mark.parametrize("model, cache_price", [
    ("gpt-6.1-sol", 0.1), ("openai/gpt-6.1-sol-20261001", 0.1),
    ("chatgpt/gpt-6.1-sol", 0.1), ("claude-sonnet-5-5", 0.1),
    ("claude-sonnet-5-5-20260928", 0.1), ("claude-haiku-5-5", 0.01),
    ("claude-mythos-5-1", 0.25), ("glm-5.2", 0.26), ("glm-5.3", 0.26),
    ("glm-5.3-flash", 0.03), ("glm-5.3-flashx", 0.075),
])
@pytest.mark.parametrize("stale", [False, True])
def test_new_prices_survive_old_cache_offline(tmp_path, monkeypatch, model, cache_price, stale):
    cache = tmp_path / "pricing_cache.json"
    cache.write_text('{}', encoding="utf-8")
    monkeypatch.setattr(cost, "CACHE_PATH", cache)
    monkeypatch.setattr(cost, "_pricing", None)
    monkeypatch.setattr(cost, "_cache_stale", lambda: stale)

    def offline():
        if not stale:
            pytest.fail("A fresh cache must not trigger a network request")
        raise URLError("offline")

    monkeypatch.setattr(cost, "_fetch_and_cache", offline)
    assert cost.calculate_cost(entry(model, cache_read_tokens=1_000)) == pytest.approx(cache_price / 1000)


@pytest.mark.parametrize("model, threshold, rates, long_rates", [
    ("gpt-6.1-sol", 272_000, (2, 10, 2.5, 0.1), (4, 15, 5, 0.2)),
    ("claude-haiku-5-5", 100_000, (0.1, 0.5, 0.125, 0.01), (0.5, 2.5, 0.625, 0.05)),
    ("MiniMax-M3", 512_000, (0.3, 1.2, 0.375, 0.06), (0.6, 2.4, 0.75, 0.12)),
])
@pytest.mark.parametrize("extra", [0, 1])
def test_context_boundaries_count_reads_and_writes(model, threshold, rates, long_rates, extra):
    rates = long_rates if extra else rates
    usage = entry(
        model, input_tokens=threshold - 30_000 + extra, output_tokens=1_000,
        cache_creation_tokens=10_000, cache_read_tokens=20_000,
    )
    expected = sum(n * r * 1e-6 for n, r in zip(
        (usage.input_tokens, 1_000, 10_000, 20_000), rates, strict=True,
    ))
    assert cost.calculate_cost(usage) == pytest.approx(expected)


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "claude-haiku-5-5", "MiniMax-M3"])
def test_new_context_tiers_use_codex_requests_not_session_totals(model):
    segments = tuple(UsageSegment(datetime(2026, 10, 9, tzinfo=UTC), 80_000, 1_000) for _ in range(8))
    usage = entry(model, agent_id="codex", input_tokens=640_000, output_tokens=8_000, pricing_segments=segments)
    expected = sum(cost.calculate_cost(entry(model, input_tokens=80_000, output_tokens=1_000)) for _ in segments)
    assert cost.calculate_cost(usage) == pytest.approx(expected)
    usage.pricing_segments = ()
    assert cost.calculate_cost(usage) == pytest.approx(expected)
    usage.pricing_segments = segments[:1]
    assert cost.calculate_cost(usage) == pytest.approx(expected)


@pytest.mark.parametrize("model, cached", [
    ("claude-sonnet-5", 0.2), ("claude-sonnet-5-20260801", 0.2),
    ("claude-haiku-4-5-20251001", 0.1), ("claude-mythos-5", 1),
    ("claude-opus-5", 0.5), ("claude-opus-5-5", 0.2),
    ("gpt-6-sol", 0.2), ("gpt-6.1-sol", 0.1),
])
def test_historical_model_cache_prices_are_isolated(model, cached):
    assert cost.calculate_cost(entry(model, cache_read_tokens=1_000)) == pytest.approx(cached / 1000)


@pytest.mark.parametrize("model, cached", [
    ("claude-sonnet-6", 0.1), ("claude-haiku-6", 0.01), ("claude-mythos-6", 0.25),
])
def test_latest_family_cache_discounts(model, cached):
    assert cost.calculate_cost(entry(model, cache_read_tokens=1_000)) == pytest.approx(cached / 1000)


@pytest.mark.parametrize("model, cache_read", [
    ("MiniMax-M2.1-highspeed", 0.03), ("MiniMax-M2.5-highspeed", 0.03),
    ("MiniMax-M2.7-highspeed", 0.06), ("minimax-m2.7-highspeed", 0.06),
])
def test_minimax_highspeed_does_not_use_standard_rate(model, cache_read):
    usage = entry(model, input_tokens=1_000, output_tokens=1_000, cache_creation_tokens=1_000, cache_read_tokens=1_000)
    assert cost.calculate_cost(usage) == pytest.approx((0.6 + 2.4 + 0.375 + cache_read) / 1000)


@pytest.mark.parametrize("model, expected_cny", [
    ("mimo-v2.6-pro", 9.025), ("mimo-v2.6-pro-20261001", 9.025),
    ("mimo-v2.6-flash", 3.02), ("mimo-v2.5-pro", 9.025), ("mimo-v2.5", 3.02),
])
def test_mimo_new_pro_has_its_own_price(model, expected_cny):
    usage = entry(model, input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert cost.calculate_cost(usage) == pytest.approx(expected_cny / 7.1)


@pytest.mark.parametrize("model", ["gemini-3.6-flash", "gemini-3.8-flash"])
@pytest.mark.parametrize("timestamp, multiplier", [
    (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), 1),
    (datetime(2027, 1, 1, tzinfo=UTC), 2),
    (datetime(2027, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))), 2),
    (datetime(2027, 1, 1), 2),
])
def test_gemini_future_price_does_not_reprice_history(monkeypatch, model, timestamp, multiplier):
    # 即使在线表已更新成未来价，历史请求仍用其发生时的价格。
    cost._pricing[model] = cost._usd(1.5, 7.5, 0.15)
    usage = entry(model, timestamp=timestamp, input_tokens=1_000, output_tokens=1_000, cache_read_tokens=1_000)
    assert cost.calculate_cost(usage) == pytest.approx(0.004575 * multiplier)


@pytest.mark.parametrize("day, off_peak", [
    ((2026, 9, 24), False), ((2026, 9, 25), True), ((2026, 9, 28), False),
    ((2026, 9, 30), False), ((2026, 10, 1), True), ((2026, 10, 7), True),
    ((2026, 10, 8), False), ((2026, 10, 10), True),
])
@pytest.mark.parametrize("model, peak_cny", [("deepseek-flash", 10.04), ("deepseek-v4-pro", 36.3)])
def test_deepseek_holidays_and_makeup_weekend_remain_off_peak(day, off_peak, model, peak_cny):
    usage = entry(
        model, timestamp=datetime(*day, 2, tzinfo=UTC), input_tokens=1_000_000,
        output_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(usage) == pytest.approx(peak_cny * (0.5 if off_peak else 1) / 7.1)


def test_deepseek_holiday_codex_segments_and_timezone():
    segments = (
        UsageSegment(datetime(2026, 9, 30, 2, tzinfo=UTC), 1_000, 1_000),
        UsageSegment(datetime(2026, 10, 1, 10, tzinfo=timezone(timedelta(hours=8))), 1_000, 1_000),
    )
    usage = entry("deepseek-flash", agent_id="codex", input_tokens=2_000, output_tokens=2_000, pricing_segments=segments)
    assert cost.calculate_cost(usage) == pytest.approx(0.015 / 7.1)


@pytest.mark.parametrize("model, boundary, lower, higher", [
    ("qwen3-coder-plus", 32_000, (4, 16, 0.4), (6, 24, 0.6)),
    ("qwen3-coder-plus", 128_000, (6, 24, 0.6), (10, 40, 1)),
    ("qwen3-coder-plus", 256_000, (10, 40, 1), (20, 200, 2)),
    ("qwen3-coder-flash", 32_000, (1, 4, 0.2), (1.5, 6, 0.3)),
    ("qwen3-coder-flash", 128_000, (1.5, 6, 0.3), (2.5, 10, 0.5)),
    ("qwen3-coder-flash", 256_000, (2.5, 10, 0.5), (5, 25, 1)),
    ("qwen3.6-plus", 256_000, (2, 12, 0.4), (8, 48, 1.6)),
    ("qwen3.7-plus", 256_000, (1.6, 6.4, 0.32), (4.8, 19.2, 0.96)),
    ("qwen3.7-plus-2026-05-26", 256_000, (2, 8, 0.4), (6, 24, 1.2)),
])
@pytest.mark.parametrize("extra", [0, 1])
def test_qwen_domestic_tiers_and_snapshot_prices(model, boundary, lower, higher, extra):
    input_rate, output_rate, cache_rate = higher if extra else lower
    usage = entry(model, input_tokens=boundary - 1_000 + extra, output_tokens=1_000, cache_read_tokens=1_000)
    expected = ((boundary - 1_000 + extra) * input_rate + 1_000 * (output_rate + cache_rate)) / 7.1e6
    assert cost.calculate_cost(usage) == pytest.approx(expected)


def tiered_info():
    return {"tiered_pricing": [
        {"range": [32_000, 128_000], **cost._usd(0.5, 2.5, 0.1)},
        {"range": [0, 32_000], **cost._usd(0.3, 1.5, 0.06)},
    ]}


@pytest.mark.parametrize("prompt, rates", [(0, (0.3, 1.5)), (32_000, (0.3, 1.5)), (32_001, (0.5, 2.5))])
def test_litellm_tiered_only_prices_do_not_return_zero(monkeypatch, prompt, rates):
    monkeypatch.setattr(cost, "_pricing", {"dashscope/qwen-test": tiered_info()})
    usage = entry("qwen-test", input_tokens=prompt, output_tokens=1_000)
    assert cost.calculate_cost(usage) == pytest.approx(prompt * rates[0] * 1e-6 + rates[1] / 1000)


def test_litellm_tiers_count_cache_and_price_codex_per_segment(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", {"dashscope/qwen-test": tiered_info()})
    segments = (
        UsageSegment(datetime(2026, 10, 9, tzinfo=UTC), 30_000, 1_000, 1_000, 1_000),
        UsageSegment(datetime(2026, 10, 9, tzinfo=UTC), 30_001, 1_000, 1_000, 1_000),
    )
    usage = entry("qwen-test", agent_id="codex", input_tokens=60_001, output_tokens=2_000,
                  cache_creation_tokens=2_000, cache_read_tokens=2_000, pricing_segments=segments)
    assert cost.calculate_cost(usage) == pytest.approx(0.010935 + 0.0182255)
    usage.pricing_segments = ()
    assert cost.calculate_cost(usage) == pytest.approx(60_001 * 0.3e-6 + 0.003 + 0.00075 + 0.00012)


@pytest.mark.parametrize("invalid", [None, {}, "bad", [None, {}, {"range": [0, "32k"]}], [
    {"range": [32_000, 0], **cost._usd(1, 2)},
    {"range": [0, 32_000], "input_cost_per_token": -1, "output_cost_per_token": 1},
    {"range": [0, 32_000], "input_cost_per_token": float("nan"), "output_cost_per_token": 1},
]])
def test_invalid_online_tiers_preserve_base_prices(monkeypatch, invalid):
    monkeypatch.setattr(cost, "_pricing", {"qwen-test": {**cost._usd(1, 2), "tiered_pricing": invalid}})
    assert cost.calculate_cost(entry("qwen-test", input_tokens=1_000, output_tokens=1_000)) == pytest.approx(0.003)


def test_unusable_tiered_only_prices_warn(monkeypatch, capsys):
    monkeypatch.setattr(cost, "_pricing", {"qwen-invalid": {"tiered_pricing": [None]}})
    monkeypatch.setattr(cost, "_warned_unknown_models", set())
    assert cost.calculate_cost(entry("qwen-invalid", input_tokens=1_000)) == 0
    assert "缺少定价" in capsys.readouterr().err


@pytest.mark.parametrize("model", ["gpt-6.1-solar", "gpt-6.10-sol", "third-party/gpt-6.1-sol"])
def test_similar_and_third_party_gpt_models_are_not_mispriced(model):
    assert cost._resolve_model_key_uncached(model, cost._fallback_pricing()) is None


def test_new_builtin_models_have_short_names():
    for model in (
        "gpt-6.1-sol", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-mythos-5-1",
        "glm-5.2", "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "qwen3.6-plus", "qwen3.7-plus",
        "qwen3-coder-flash", "MiniMax-M2.1-highspeed", "MiniMax-M2.5-highspeed", "MiniMax-M2.7-highspeed",
        "mimo-v2.6-pro", "mimo-v2.6-flash", "gemini-3.6-flash", "gemini-3.8-flash",
    ):
        assert model in MODEL_SHORT
