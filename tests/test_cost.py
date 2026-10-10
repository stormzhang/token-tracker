import http.client
import json
from datetime import UTC, datetime
from urllib.error import URLError

import pytest

from token_tracker.adapters.types import UsageEntry, UsageSegment
from token_tracker.analyzer import cost


def make_entry(**kw):
    defaults = dict(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="s1",
        message_id="m1",
        request_id="r1",
        model="claude-opus-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        cost_usd=None,
        project="proj",
        agent_id="claude",
    )
    defaults.update(kw)
    return UsageEntry(**defaults)


@pytest.fixture
def fixed_pricing(monkeypatch):
    """Inject deterministic pricing so tests never hit the network or cache file."""
    pricing = {
        "claude-opus-4-6": {
            "input_cost_per_token": 15e-6,
            "output_cost_per_token": 75e-6,
            "cache_creation_input_token_cost": 18.75e-6,
            "cache_read_input_token_cost": 1.5e-6,
        },
    }
    monkeypatch.setattr(cost, "_pricing", pricing)
    return pricing


def test_explicit_cost_is_passed_through(fixed_pricing):
    # When the entry already carries a cost, pricing must be ignored entirely.
    entry = make_entry(cost_usd=1.23, input_tokens=999_999)
    assert cost.calculate_cost(entry) == 1.23


def test_cost_computed_from_pricing(fixed_pricing):
    entry = make_entry(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # 15 + 75 + 18.75 + 1.5
    assert cost.calculate_cost(entry) == pytest.approx(110.25)


def test_unknown_model_returns_zero(fixed_pricing):
    entry = make_entry(model="totally-unknown-xyz", input_tokens=1_000_000)
    assert cost.calculate_cost(entry) == 0.0


def test_model_resolved_by_substring(fixed_pricing):
    # A dated model id should resolve to its base pricing key via prefix match.
    entry = make_entry(model="claude-opus-4-6-20260101", input_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(15.0)


@pytest.fixture
def openai_pricing(monkeypatch):
    pricing = {
        "gpt-5": {"input_cost_per_token": 1.25e-6, "output_cost_per_token": 10e-6},
        "gpt-5-mini": {"input_cost_per_token": 0.25e-6, "output_cost_per_token": 2e-6},
        "gpt-5-codex": {"input_cost_per_token": 1.25e-6, "output_cost_per_token": 10e-6},
    }
    monkeypatch.setattr(cost, "_pricing", pricing)
    return pricing


def test_gpt5_exact_match_does_not_leak_to_mini(openai_pricing):
    # Regression: old `in` substring matching could resolve "gpt-5" against
    # "gpt-5-mini" first and silently use the cheaper mini pricing.
    entry = make_entry(model="gpt-5", input_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(1.25)


def test_dated_variant_resolves_to_longest_base(openai_pricing):
    # A dated codex id should resolve to gpt-5-codex (length 11), not gpt-5 (length 5).
    entry = make_entry(model="gpt-5-codex-2025-12-01", input_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(1.25)


def test_base_name_resolves_to_dated_pricing(openai_pricing, monkeypatch):
    # Reverse-direction fallback: pricing only has dated keys, model is the base name.
    pricing = {
        "gpt-5-2025-08-07": {"input_cost_per_token": 1.25e-6, "output_cost_per_token": 10e-6},
        "gpt-5-mini-2025-08-07": {"input_cost_per_token": 0.25e-6, "output_cost_per_token": 2e-6},
    }
    monkeypatch.setattr(cost, "_pricing", pricing)
    entry = make_entry(model="gpt-5", input_tokens=1_000_000)
    # Must pick gpt-5-2025-08-07 (shorter), not gpt-5-mini-2025-08-07.
    assert cost.calculate_cost(entry) == pytest.approx(1.25)


def test_fallback_pricing_includes_openai_models():
    pricing = cost._fallback_pricing()
    for k in (
        "gpt-5", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
        "gpt-6-sol", "gpt-6-luna",
        "gpt-5-codex", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "codex-mini-latest",
    ):
        assert k in pricing, f"fallback pricing missing {k}"
        assert pricing[k].get("input_cost_per_token", 0) > 0


def test_gpt55_priced_4x_gpt5(monkeypatch):
    # gpt-5.5 价格是 gpt-5 的 4 倍，不可走 gpt-5 系列兜底，必须有专属内置价
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="gpt-5.5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(35.0)  # 5 + 30


def test_gpt56_tiers_priced_per_tier_not_swallowed_by_gpt5(monkeypatch):
    # GPT-5.6 三档是独立定价："gpt-5.6-*" 不能被 "gpt-5" 前缀吞掉。
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    sol = make_entry(model="gpt-5.6-sol", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(sol) == pytest.approx(8.0 + 30.0)
    terra = make_entry(model="gpt-5.6-terra", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(terra) == pytest.approx(4.0 + 18.0)
    luna = make_entry(model="gpt-5.6-luna", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(luna) == pytest.approx(0.4 + 1.8)
    # dated / variant 后缀靠前缀命中本档，不被 gpt-5 吞
    dated = make_entry(model="gpt-5.6-terra-20260709", input_tokens=1_000_000)
    assert cost.calculate_cost(dated) == pytest.approx(4.0)
    # 裸 "gpt-5.6"（无档位后缀）反向兜底到最短 key，即旗舰 sol
    bare = make_entry(model="gpt-5.6", input_tokens=1_000_000)
    assert cost.calculate_cost(bare) == pytest.approx(8.0)
    # 系列内未知新档退回旗舰 sol 价（宁可高估不低估），不按 gpt-5 错价、不归零
    nova = make_entry(model="gpt-5.6-nova", input_tokens=1_000_000)
    assert cost.calculate_cost(nova) == pytest.approx(8.0)


def test_gpt56_long_context_uses_request_tier(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    at_boundary = make_entry(
        model="gpt-5.6-sol", input_tokens=271_000, output_tokens=1_000_000,
        cache_read_tokens=1_000,
    )
    assert cost.calculate_cost(at_boundary) == pytest.approx(271_000 * 4e-6 + 20 + 0.0004)

    above_boundary = make_entry(
        model="gpt-5.6-sol", input_tokens=271_001, output_tokens=1_000_000,
        cache_read_tokens=1_000,
    )
    assert cost.calculate_cost(above_boundary) == pytest.approx(271_001 * 8e-6 + 30 + 0.0008)


@pytest.mark.parametrize("model, expected", [("gpt-5.6-sol", 1.6), ("gpt-6-astra", 4.0)])
def test_codex_long_context_is_priced_per_request_not_session_total(monkeypatch, model, expected):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    segments = (
        UsageSegment(datetime(2026, 8, 20, tzinfo=UTC), 150_000, 10_000),
        UsageSegment(datetime(2026, 8, 20, 1, tzinfo=UTC), 150_000, 10_000),
    )
    entry = make_entry(
        model=model, agent_id="codex", input_tokens=300_000, output_tokens=20_000,
        pricing_segments=segments,
    )
    assert cost.calculate_cost(entry) == pytest.approx(expected)


@pytest.mark.parametrize("segments", [(), (UsageSegment(datetime(2026, 9, 9, tzinfo=UTC), 300_000, 1),)])
def test_astra_missing_request_usage_uses_base_price(monkeypatch, segments):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model="gpt-6-astra", agent_id="codex", input_tokens=600_000, output_tokens=2,
        pricing_segments=segments,
    )
    assert cost.calculate_cost(entry) == pytest.approx(6.0001)


def test_gpt56_and_opus5_have_short_names():
    from token_tracker.ui.format import MODEL_SHORT
    for k in (
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
        "gpt-6-sol", "gpt-6-luna", "claude-opus-5", "claude-opus-5-5",
    ):
        assert k in MODEL_SHORT, f"MODEL_SHORT missing {k}"


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-astra-20260901"])
@pytest.mark.parametrize("extra_input, expected", [(0, 1.64), (1, 3.23002)])
def test_astra_context_boundary_includes_both_cache_buckets(monkeypatch, model, extra_input, expected):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model=model, input_tokens=100_000 + extra_input, output_tokens=2_000,
        cache_creation_tokens=32_000, cache_read_tokens=140_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(expected)


@pytest.mark.parametrize("model, base_cost, long_cost", [
    ("gpt-6-sol", 0.328, 0.646004),
    ("gpt-6-luna", 0.0164, 0.0323002),
])
def test_gpt6_sol_luna_context_boundary(monkeypatch, model, base_cost, long_cost):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    usage = dict(
        output_tokens=2_000, cache_creation_tokens=32_000, cache_read_tokens=140_000,
    )
    at_boundary = make_entry(model=model, input_tokens=100_000, **usage)
    above_boundary = make_entry(model=model, input_tokens=100_001, **usage)
    assert cost.calculate_cost(at_boundary) == pytest.approx(base_cost)
    assert cost.calculate_cost(above_boundary) == pytest.approx(long_cost)


@pytest.mark.parametrize("model, expected", [
    ("claude-opus-5-5", 29.2),
    ("claude-opus-5-5-20260922", 29.2),
    ("claude-opus-5", 36.75),
    ("claude-opus-5-20260724", 36.75),
])
def test_opus55_preserves_opus5_historical_price(monkeypatch, model, expected):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model=model, input_tokens=1_000_000, output_tokens=1_000_000,
        cache_creation_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(expected)


@pytest.mark.parametrize("model, expected", [
    ("claude-fable-5-1", 72.75),
    ("claude-fable-5-1-20260901", 72.75),
    ("claude-fable-5", 73.5),
    ("claude-fable-5-20260609", 73.5),
    ("claude-mythos-5", 73.5),
])
def test_fable51_cache_discount_preserves_old_models(monkeypatch, model, expected):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model=model, input_tokens=1_000_000, output_tokens=1_000_000,
        cache_creation_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(expected)


@pytest.mark.parametrize("model, expected", [
    ("gpt-6-astra", 0.001), ("claude-fable-5-1", 0.00025),
    ("gpt-6-sol", 0.0002), ("gpt-6-luna", 0.00001),
    ("claude-opus-5-5", 0.0002), ("grok-4.7", 0.0005),
])
@pytest.mark.parametrize("stale", [False, True])
def test_new_models_price_with_old_cache_and_no_network(tmp_path, monkeypatch, model, expected, stale):
    cache = tmp_path / "pricing_cache.json"
    cache.write_text(json.dumps({
        "claude-fable-5": cost._fallback_pricing()["claude-fable-5"],
        "claude-opus-5": cost._fallback_pricing()["claude-opus-5"],
        "grok-4.3": cost._fallback_pricing()["grok-4.3"],
    }))
    monkeypatch.setattr(cost, "CACHE_PATH", cache)
    monkeypatch.setattr(cost, "_cache_stale", lambda: stale)
    monkeypatch.setattr(cost, "_pricing", None)

    def offline():
        if not stale:
            pytest.fail("Fresh cache should not fetch")
        raise URLError("offline")

    monkeypatch.setattr(cost, "_fetch_and_cache", offline)
    assert cost.calculate_cost(make_entry(model=model, cache_read_tokens=1_000)) == pytest.approx(expected)


def test_new_models_short_names_and_astra_prefix_boundary():
    from token_tracker.ui.format import _model_short

    assert _model_short("gpt-6-astra") == "GPT-6 Astra"
    assert _model_short("gpt-6-sol") == "GPT-6 Sol"
    assert _model_short("gpt-6-luna") == "GPT-6 Luna"
    assert _model_short("claude-opus-5-5") == "Opus 5.5"
    assert _model_short("claude-fable-5-1") == "Fable 5.1"
    pricing = cost._fallback_pricing()
    assert cost._resolve_model_key("gpt-6-astral", pricing) is None
    assert cost._resolve_model_key("gpt-60-astra", pricing) is None
    assert cost._resolve_model_key("gpt-6-solar", pricing) is None
    assert cost._resolve_model_key("gpt-6-lunar", pricing) is None


@pytest.mark.parametrize("namespace", ["chatgpt", "openai"])
@pytest.mark.parametrize("model", [
    "gpt-5.6-sol", "gpt-5.6-sol-20260901", "gpt-6-astra",
    "gpt-6-sol", "gpt-6-luna", "gpt-6-luna-20260922",
])
@pytest.mark.parametrize("prompt_tokens", [272_000, 272_001])
def test_openai_namespaced_models_keep_base_pricing(monkeypatch, capsys, namespace, model, prompt_tokens):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    monkeypatch.setattr(cost, "_model_key_cache", {})
    monkeypatch.setattr(cost, "_warned_unknown_models", set())
    usage = dict(
        input_tokens=prompt_tokens - 30_000, output_tokens=2_000,
        cache_creation_tokens=10_000, cache_read_tokens=20_000,
    )
    expected = cost.calculate_cost(make_entry(model=model, **usage))
    assert expected > 0
    assert cost.calculate_cost(make_entry(model=f"{namespace}/{model}", **usage)) == pytest.approx(expected)
    assert capsys.readouterr().err == ""


def test_namespaced_exact_price_overrides_cached_base_resolution(monkeypatch):
    pricing = cost._fallback_pricing()
    model = "chatgpt/gpt-5.6-sol"
    monkeypatch.setattr(cost, "_pricing", pricing)
    monkeypatch.setattr(cost, "_model_key_cache", {})
    entry = make_entry(model=model, input_tokens=1_000)
    assert cost.calculate_cost(entry) == pytest.approx(0.004)
    pricing[model] = {"input_cost_per_token": 9e-6}
    assert cost.calculate_cost(entry) == pytest.approx(0.009)


def test_namespaced_dated_price_precedes_bare_price(monkeypatch):
    pricing = cost._fallback_pricing()
    pricing["chatgpt/gpt-5.6-sol"] = {"input_cost_per_token": 9e-6}
    monkeypatch.setattr(cost, "_pricing", pricing)
    monkeypatch.setattr(cost, "_model_key_cache", {})
    entry = make_entry(model="chatgpt/gpt-5.6-sol-20260901", input_tokens=1_000)
    assert cost.calculate_cost(entry) == pytest.approx(0.009)


@pytest.mark.parametrize("model", [
    "third-party/gpt-5.6-sol", "chatgpt-pro/gpt-5.6-sol", "chatgpt/deepseek-v4-flash",
    "chatgpt/openai/gpt-5.6-sol", "chatgpt/", "chatgpt/gpt-6-astral",
    "chatgpt/gpt-6-solar", "third-party/deepseek-flash", "",
])
def test_namespace_fallback_does_not_strip_unknown_or_nested_prefixes(model):
    assert cost._resolve_model_key_uncached(model, cost._fallback_pricing()) is None


def test_opus5_falls_back_to_opus_family_pricing(monkeypatch):
    # Opus 5 与 Opus 4.8 同价（$5/$25），老系列新版本靠家族兜底即可，无需专属内置价
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(5.0 + 25.0)


def test_codex_auto_review_falls_back_to_gpt56_sol(monkeypatch):
    # Codex stop-time auto-review 用虚拟 model name codex-auto-review，按当代旗舰（gpt-5.6-sol）价兜底
    # （不归零）。
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model="codex-auto-review", agent_id="codex",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(24.0)


def test_fallback_pricing_includes_fable():
    # Fable 5 是全新系列，必须有专属兜底价（不能退回 Opus，价格差一倍）
    info = cost._fallback_pricing()["claude-fable-5"]
    assert info["input_cost_per_token"] == pytest.approx(10e-6)
    assert info["output_cost_per_token"] == pytest.approx(50e-6)


def test_mythos_uses_fable_pricing(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="claude-mythos-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(10.0 + 50.0)


def test_fable_cost_is_double_opus(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model="claude-fable-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # 10 + 50 + 12.5 + 1.0
    assert cost.calculate_cost(entry) == pytest.approx(73.5)


def test_fable_dated_variant_resolves_via_prefix(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="claude-fable-5-20260601", input_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(10.0)


def test_fallback_pricing_includes_sonnet_5_permanent_price():
    # Anthropic 已宣布 Sonnet 5 的 $2/$10 价格永久生效。
    info = cost._fallback_pricing()["claude-sonnet-5"]
    assert info["input_cost_per_token"] == pytest.approx(2e-6)
    assert info["output_cost_per_token"] == pytest.approx(10e-6)
    assert info["cache_creation_input_token_cost"] == pytest.approx(2.5e-6)
    assert info["cache_read_input_token_cost"] == pytest.approx(0.2e-6)


def test_sonnet_5_cost_uses_permanent_price(monkeypatch):
    # 1M input + 1M output → 2 + 10 = 12 USD。
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(12.0)


def test_sonnet_family_fallback_points_to_latest_sonnet(monkeypatch):
    # 未来变体退回 Sonnet 5.5，旧 Sonnet 5 的精确报价独立保留。
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="claude-sonnet-6-20270101", input_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(2.0)


def test_unknown_fable_variant_falls_back_to_family(monkeypatch):
    # 未来 Fable 变体退回 5.1 的最新已知价，旧版精确 key 仍保留旧价。
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="claude-fable-6-20270101", input_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(10.25)


def test_unknown_model_warns_once(fixed_pricing, monkeypatch, capsys):
    # 全新系列接不住时按 $0 计，但必须显形提醒；同一模型只提示一次
    monkeypatch.setattr(cost, "_warned_unknown_models", set())
    assert cost.calculate_cost(make_entry(model="claude-quartz-1", input_tokens=1_000_000)) == 0.0
    assert cost.calculate_cost(make_entry(model="claude-quartz-1", input_tokens=500_000)) == 0.0
    err = capsys.readouterr().err
    assert err.count("claude-quartz-1") == 1


def test_fresh_cache_is_used_without_fetching(tmp_path, monkeypatch):
    cache = tmp_path / "pricing_cache.json"
    cache.write_text('{"gpt-5": {"input_cost_per_token": 1e-6}}', encoding="utf-8")
    monkeypatch.setattr(cost, "CACHE_PATH", cache)
    monkeypatch.setattr(cost, "_cache_stale", lambda: False)
    # 新鲜缓存命中时绝不能联网
    monkeypatch.setattr(cost, "_fetch_and_cache", lambda: (_ for _ in ()).throw(AssertionError("不应联网")))
    assert cost._load_pricing() == {"gpt-5": {"input_cost_per_token": 1e-6}}


def test_valid_json_with_wrong_shape_is_not_a_cache(tmp_path, monkeypatch):
    cache = tmp_path / "pricing_cache.json"
    cache.write_text('["not", "a", "pricing", "mapping"]', encoding="utf-8")
    monkeypatch.setattr(cost, "CACHE_PATH", cache)
    assert cost._read_cache() is None


def test_stale_cache_kept_when_fetch_fails(tmp_path, monkeypatch):
    # 关键：缓存过期但联网失败时，应保留旧缓存而非掉到内置兜底
    cache = tmp_path / "pricing_cache.json"
    cache.write_text('{"gpt-5": {"input_cost_per_token": 9e-6}}', encoding="utf-8")
    monkeypatch.setattr(cost, "CACHE_PATH", cache)
    monkeypatch.setattr(cost, "_cache_stale", lambda: True)

    def boom():
        raise URLError("offline")

    monkeypatch.setattr(cost, "_fetch_and_cache", boom)
    assert cost._load_pricing() == {"gpt-5": {"input_cost_per_token": 9e-6}}


def test_builtin_fallback_only_when_no_cache_and_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cost, "CACHE_PATH", tmp_path / "missing.json")

    def boom():
        raise TimeoutError("offline")

    monkeypatch.setattr(cost, "_fetch_and_cache", boom)
    result = cost._load_pricing()
    assert "gpt-5" in result and "claude-opus-4-7" in result  # 命中内置兜底表


@pytest.mark.parametrize("exc", [
    http.client.IncompleteRead(b""),          # 截断的 HTTP/1.1 响应
    UnicodeDecodeError("utf-8", b"", 0, 1, "bad"),  # resp.read().decode() 失败（ValueError 子类）
    json.JSONDecodeError("bad", "", 0),        # 损坏的 JSON
])
def test_stale_cache_survives_middownload_errors(tmp_path, monkeypatch, exc):
    # 回归：抓取链中途抛 HTTPException/decode/JSON 错误时，必须用旧缓存兜底而非崩溃
    cache = tmp_path / "pricing_cache.json"
    cache.write_text('{"gpt-5": {"input_cost_per_token": 7e-6}}', encoding="utf-8")
    monkeypatch.setattr(cost, "CACHE_PATH", cache)
    monkeypatch.setattr(cost, "_cache_stale", lambda: True)

    def boom():
        raise exc

    monkeypatch.setattr(cost, "_fetch_and_cache", boom)
    assert cost._load_pricing() == {"gpt-5": {"input_cost_per_token": 7e-6}}


# ---- 国产模型定价（2026-06 官方核实，详见 cost.py 注释）----


def test_fallback_pricing_includes_chinese_models():
    # 六家国产主力 model id 都要有内置价，不能因 litellm 未收录 bare key 而归零
    pricing = cost._fallback_pricing()
    for k in (
        "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5", "moonshot-v1-128k",
        "glm-4.6", "glm-4.5-air", "glm-5", "glm-5.1",
        "qwen3-coder-plus", "qwen3-coder-next", "qwen-max", "qwen-plus",
        "doubao-seed-1-6", "doubao-seed-code", "doubao-seed-2.0-code", "doubao-seed-2.1-pro",
        "doubao-1-5-pro-32k", "doubao-1-5-pro-256k",
        "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp",
        "deepseek-chat", "deepseek-reasoner",
        "MiniMax-M2", "MiniMax-M2.7", "MiniMax-M3",
        "mimo-v2.5-pro", "mimo-v2.5",
    ):
        assert k in pricing, f"fallback pricing missing {k}"
        assert pricing[k].get("input_cost_per_token", 0) > 0


def test_glm_uses_intl_usd_pricing(monkeypatch):
    # GLM 口径例外：用 z.ai 国际站官方 USD（$0.6/$2.2/$0.11），不折汇率
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model="glm-4.6", input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000
    )
    assert cost.calculate_cost(entry) == pytest.approx(0.6 + 2.2 + 0.11)


def test_kimi_cny_converted_to_usd(monkeypatch):
    # Kimi K2.7 Code 中国站 ¥6.5/¥27/¥1.3 按 7.1 折 USD
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model="kimi-k2.7-code", input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000
    )
    assert cost.calculate_cost(entry) == pytest.approx((6.5 + 27 + 1.3) / 7.1)


def test_deepseek_old_price_and_qwen_cny_base_tier(monkeypatch):
    # DeepSeek 在新价生效前仍按旧价 ¥1/¥2；Qwen3-Coder Plus 基础档 ¥4/¥16，均 ÷7.1。
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    ds = make_entry(model="deepseek-v4-flash", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(ds) == pytest.approx((1 + 2) / 7.1)
    qw = make_entry(model="qwen3-coder-plus", input_tokens=32_000, output_tokens=1_000_000)
    assert cost.calculate_cost(qw) == pytest.approx((32_000 * 4e-6 + 16) / 7.1)


@pytest.mark.parametrize(
    ("timestamp", "expected_cny"),
    [
        (datetime(2026, 8, 17, 1, 0, tzinfo=UTC), 3 + 9 + 0.1),   # 周一第一段峰时
        (datetime(2026, 8, 17, 4, 0, tzinfo=UTC), 1.5 + 4.5 + 0.05),
        (datetime(2026, 8, 17, 6, 0, tzinfo=UTC), 3 + 9 + 0.1),   # 周一第二段峰时
        (datetime(2026, 8, 17, 10, 0, tzinfo=UTC), 1.5 + 4.5 + 0.05),
        (datetime(2026, 8, 22, 1, 0, tzinfo=UTC), 1.5 + 4.5 + 0.05),  # 周六全天谷价
        (datetime(2026, 8, 23, 6, 0, tzinfo=UTC), 1.5 + 4.5 + 0.05),  # 周日全天谷价
    ],
)
def test_deepseek_flash_new_peak_off_peak_and_weekends(monkeypatch, timestamp, expected_cny):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(
        model="deepseek-v4-flash", timestamp=timestamp,
        input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(expected_cny / 7.1)


def test_deepseek_pro_new_peak_and_off_peak(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    peak = make_entry(
        model="deepseek-v4-pro", timestamp=datetime(2026, 8, 17, 1, tzinfo=UTC),
        input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(peak) == pytest.approx((9 + 27 + 0.3) / 7.1)
    weekend = make_entry(
        model="deepseek-v4-pro", timestamp=datetime(2026, 8, 22, 1, tzinfo=UTC),
        input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(weekend) == pytest.approx((4.5 + 13.5 + 0.15) / 7.1)


def test_deepseek_price_switch_timestamp(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    before = make_entry(
        model="deepseek-v4-flash", timestamp=datetime(2026, 8, 16, 15, 59, 59, tzinfo=UTC),
        input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(before) == pytest.approx((1 + 2 + 0.02) / 7.1)
    after = make_entry(
        model="deepseek-v4-flash", timestamp=datetime(2026, 8, 16, 16, tzinfo=UTC),
        input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(after) == pytest.approx((1.5 + 4.5 + 0.05) / 7.1)


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"])
def test_deepseek_v41_flash_peak_off_peak_and_old_aliases(monkeypatch, model):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    usage = dict(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    peak = make_entry(model=model, timestamp=datetime(2026, 9, 10, 6, tzinfo=UTC), **usage)
    off_peak = make_entry(model=model, timestamp=datetime(2026, 9, 10, 4, tzinfo=UTC), **usage)
    weekend = make_entry(model=model, timestamp=datetime(2026, 9, 12, 1, tzinfo=UTC), **usage)
    assert cost.calculate_cost(peak) == pytest.approx((2 + 8 + 0.04) / 7.1)
    assert cost.calculate_cost(off_peak) == pytest.approx((1 + 4 + 0.02) / 7.1)
    assert cost.calculate_cost(weekend) == pytest.approx((1 + 4 + 0.02) / 7.1)


def test_deepseek_v41_switch_preserves_prior_flash_and_pro_prices(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    usage = dict(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    before = make_entry(
        model="deepseek-v4-flash", timestamp=datetime(2026, 9, 10, 3, 59, 59, tzinfo=UTC),
        **usage,
    )
    pro = make_entry(model="deepseek-v4-pro", timestamp=datetime(2026, 9, 10, 6, tzinfo=UTC), **usage)
    assert cost.calculate_cost(before) == pytest.approx((3 + 9 + 0.1) / 7.1)
    assert cost.calculate_cost(pro) == pytest.approx((9 + 27 + 0.3) / 7.1)


def test_deepseek_v41_codex_segments_use_request_time_with_old_cache(monkeypatch):
    stale = cost._fallback_pricing()
    stale["deepseek-flash"] = {
        "input_cost_per_token": 99e-6, "output_cost_per_token": 99e-6,
    }
    monkeypatch.setattr(cost, "_pricing", stale)
    segments = (
        UsageSegment(datetime(2026, 9, 10, 3, 59, 59, tzinfo=UTC), 1_000_000, 1_000_000),
        UsageSegment(datetime(2026, 9, 10, 4, tzinfo=UTC), 1_000_000, 1_000_000),
    )
    entry = make_entry(
        model="deepseek-v4-flash", agent_id="codex",
        input_tokens=2_000_000, output_tokens=2_000_000, pricing_segments=segments,
    )
    assert cost.calculate_cost(entry) == pytest.approx((3 + 9 + 1 + 4) / 7.1)
    current = make_entry(
        model="deepseek-flash", timestamp=datetime(2026, 9, 10, 4, tzinfo=UTC),
        input_tokens=1_000_000,
    )
    assert cost.calculate_cost(current) == pytest.approx(1 / 7.1)


def test_deepseek_v41_official_provider_key_uses_off_peak_price(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", {
        "deepseek/deepseek-flash": {"input_cost_per_token": 99e-6},
    })
    entry = make_entry(
        model="deepseek/deepseek-flash", timestamp=datetime(2026, 9, 10, 4, tzinfo=UTC),
        input_tokens=1_000_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(1 / 7.1)


def test_deepseek_v4_dynamic_price_does_not_override_explicit_old_model(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", {
        "deepseek-v3.2": {"input_cost_per_token": 7e-6, "output_cost_per_token": 11e-6},
    })
    entry = make_entry(
        model="deepseek-v3.2", timestamp=datetime(2026, 8, 22, 1, tzinfo=UTC),
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert cost.calculate_cost(entry) == pytest.approx(18.0)


def test_qwen_and_doubao_long_context_tiers(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    qwen = make_entry(model="qwen3-coder-next", input_tokens=128_001, output_tokens=1_000_000)
    assert cost.calculate_cost(qwen) == pytest.approx((128_001 * 2.5e-6 + 10) / 7.1)
    doubao = make_entry(
        model="doubao-seed-2.0-code", input_tokens=128_001, output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(doubao) == pytest.approx((128_001 * 9.6e-6 + 48 + 1.92) / 7.1)


def test_minimax_m2_usd_pricing(monkeypatch):
    # MiniMax M2 官方 USD $0.3/$1.2（与中国站÷7 自洽）
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="MiniMax-M2", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(0.3 + 1.2)


def test_mimo_cny_pricing(monkeypatch):
    # 小米 MiMo 官方中国站人民币价（mimo.mi.com）：Pro ¥3/¥6、标准 ¥1/¥2，÷7.1；未来版本系列兜底
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    pro = make_entry(model="mimo-v2.5-pro", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(pro) == pytest.approx((3 + 6) / 7.1)
    std = make_entry(model="mimo-v2.5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost.calculate_cost(std) == pytest.approx((1 + 2) / 7.1)
    # 未来 mimo-v3 → mimo-v2.5 系列兜底（¥1 input ÷7.1）
    assert cost.calculate_cost(make_entry(model="mimo-v3", input_tokens=1_000_000)) == pytest.approx(1 / 7.1)


def test_chinese_model_family_fallback(monkeypatch):
    # 未知新版本 / 已下线旧 id 按系列兜底，不归零
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    # 未来 kimi-k4 → kimi-k2.6（¥6.5 input ÷7.1）
    assert cost.calculate_cost(make_entry(model="kimi-k4-preview", input_tokens=1_000_000)) == pytest.approx(6.5 / 7.1)
    # 已 EOL 的 kimi-k2-instruct → kimi 系列兜底，不归零
    assert cost.calculate_cost(make_entry(model="kimi-k2-instruct", input_tokens=1_000_000)) == pytest.approx(6.5 / 7.1)
    # 未来 glm-4.8 → glm-4.6（$0.6 input，不折汇率）
    assert cost.calculate_cost(make_entry(model="glm-4.8", input_tokens=1_000_000)) == pytest.approx(0.6)
    # 未来 minimax-m4 → MiniMax-M2（$0.3 input）
    assert cost.calculate_cost(make_entry(model="minimax-m4", input_tokens=1_000_000)) == pytest.approx(0.3)


def test_kimi_k3_priced_by_official_usd_rate(monkeypatch):
    # kimi-k3 是全新旗舰（2026-07-16 上线）：官方国际站 $3/$15，与 k2.6 档（≈$0.92/$3.8）价差 3 倍+，
    # 必须有专属内置价，不能走 kimi 家族兜底；Kimi Code wire 的 alias id "kimi-code/k3" 路由到同一档
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    entry = make_entry(model="kimi-k3", input_tokens=1_000_000, output_tokens=1_000_000,
                       cache_read_tokens=1_000_000)
    assert cost.calculate_cost(entry) == pytest.approx(3.0 + 15.0 + 0.3)
    alias = make_entry(model="kimi-code/k3", input_tokens=1_000_000, output_tokens=1_000_000,
                       cache_read_tokens=1_000_000)
    assert cost.calculate_cost(alias) == pytest.approx(3.0 + 15.0 + 0.3)


def test_chinese_models_have_short_names():
    # cost.py 内置的国产 key 都应在 MODEL_SHORT 有短名（报表 / 状态栏可读）
    from token_tracker.ui.format import MODEL_SHORT
    for k in (
        "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
        "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
        "glm-4.6", "glm-4.5", "glm-4.5-air", "glm-4.7", "glm-5", "glm-5.1",
        "qwen3-coder-plus", "qwen3-coder-next", "qwen-max", "qwen-plus",
        "doubao-seed-1-6", "doubao-seed-code", "doubao-seed-2.0-code", "doubao-seed-2.1-pro",
        "doubao-1-5-pro-32k", "doubao-1-5-pro-256k",
        "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp",
        "deepseek-chat", "deepseek-reasoner",
        "MiniMax-M2", "MiniMax-M2.1", "MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3",
        "mimo-v2.5-pro", "mimo-v2.5",
    ):
        assert k in MODEL_SHORT, f"MODEL_SHORT missing {k}"


def test_grok_pricing_and_retirement_routing(monkeypatch):
    # xAI Grok 官方 USD（docs.x.ai）；2026-05-15 退役 slug 按官方路由到 grok-4.3 / grok-build-0.1
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    flagship = make_entry(model="grok-4.3", input_tokens=100_000, output_tokens=1_000_000)
    assert cost.calculate_cost(flagship) == pytest.approx(0.125 + 2.5)
    coding = make_entry(model="grok-build-0.1", input_tokens=100_000, output_tokens=1_000_000)
    assert cost.calculate_cost(coding) == pytest.approx(0.1 + 2.0)
    # 退役别名 grok-code-fast-1 → build-0.1 价（¥ 无关，纯 USD）
    alias = make_entry(model="grok-code-fast-1", input_tokens=100_000, output_tokens=1_000_000)
    assert cost.calculate_cost(alias) == pytest.approx(2.1)
    # 退役 slug grok-4-fast / grok-3 → grok-4.3 价（官方就这么路由）
    assert cost.calculate_cost(make_entry(model="grok-4-fast", input_tokens=1_000_000)) == pytest.approx(2.5)
    assert cost.calculate_cost(make_entry(model="grok-3", input_tokens=1_000_000)) == pytest.approx(2.5)


def test_grok_new_models_and_long_context(monkeypatch):
    monkeypatch.setattr(cost, "_pricing", cost._fallback_pricing())
    base = make_entry(model="grok-4.6", input_tokens=199_999, output_tokens=1_000_000)
    assert cost.calculate_cost(base) == pytest.approx(199_999 * 2e-6 + 6)
    long = make_entry(
        model="grok-4.6", input_tokens=200_000, output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert cost.calculate_cost(long) == pytest.approx(200_000 * 4e-6 + 12 + 1)
    latest = make_entry(model="grok-4.7", input_tokens=200_000, output_tokens=1_000_000,
                        cache_read_tokens=1_000_000)
    assert cost.calculate_cost(latest) == pytest.approx(200_000 * 4e-6 + 12 + 1)
    assert cost.calculate_cost(make_entry(model="grok-4.7", input_tokens=199_999)) == pytest.approx(
        199_999 * 2e-6
    )


@pytest.mark.parametrize(
    ("model", "provider_key", "expected"),
    [
        ("grok-4.6", "xai/grok-4.6", 2.0),
        ("grok-4.7", "xai/grok-4.7", 2.0),
        ("glm-5.3", "zai/glm-5.3", 1.4),
    ],
)
def test_bare_model_resolves_official_provider_key(monkeypatch, model, provider_key, expected):
    monkeypatch.setattr(cost, "_pricing", {
        provider_key: {"input_cost_per_token": expected * 1e-6, "output_cost_per_token": 0},
    })
    assert cost.calculate_cost(make_entry(model=model, input_tokens=1_000_000)) == pytest.approx(expected)


def test_gemini_and_grok_short_names():
    # Gemini 3.6 / 3.8 Flash 已补离线价，此处统一检查报表短名。
    from token_tracker.ui.format import MODEL_SHORT
    for k in (
        "gemini-2.5-pro", "gemini-3-pro-preview", "gemini-3.5-flash",
        "gemini-3.6-flash", "gemini-3.7-pro", "gemini-3.8-flash",
        "grok-4.3", "grok-4.5", "grok-4.6", "grok-4.7", "grok-build-0.1", "grok-code-fast-1",
    ):
        assert k in MODEL_SHORT, f"MODEL_SHORT missing {k}"
