import io
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from rich.text import Text

from token_tracker import cli, config, hooks, sidebar
from token_tracker.adapters import agy, agy_quota
from token_tracker.templates import agy_statusline


def test_agy_history_and_sidebar(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agy, "AGY_HOME", tmp_path)
    repo = tmp_path / "sample repo"
    (repo / ".git").mkdir(parents=True)
    assert agy.detect().id == "agy"
    with closing(sqlite3.connect(tmp_path / "conversation_summaries.db")) as db:
        db.execute("CREATE TABLE conversation_summaries(conversation_id TEXT, workspace_uris TEXT)")
        db.execute("INSERT INTO conversation_summaries VALUES (?, ?)", ("sid", json.dumps([repo.as_uri()])))
        db.commit()
    path = tmp_path / "brain/sid/.system_generated/logs/transcript.jsonl"
    path.parent.mkdir(parents=True)
    now = datetime.now(UTC)
    request = {
        "type": "PLANNER_RESPONSE", "created_at": (now - timedelta(minutes=1)).isoformat(), "step_index": 8,
        "model": "gemini-3.8-flash", "input_tokens": 100, "output_tokens": 40, "cache_read_tokens": 700,
    }
    prompt = {"created_at": (now - timedelta(minutes=2)).isoformat(), "type": "USER_INPUT", "content": "Hello"}
    rows = [
        dict(request, created_at=(now - timedelta(hours=8)).isoformat(), step_index=1),
        dict(prompt, source="USER_EXPLICIT"),
        dict(prompt, source="SYSTEM", content="Internal metadata"),
        request,
        request,
        dict(request, type="CHECKPOINT", step_index=9),
        dict(request, created_at="2026-11-25", step_index=10),
    ]
    path.write_text("invalid JSON\n" + "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    [entry] = agy.load_entries(hours_back=1)
    assert (entry.input_tokens, entry.output_tokens, entry.cache_read_tokens, entry.total_tokens) == (100, 40, 700, 840)
    assert entry.project == "sample repo"
    assert len(agy.load_recent_entries(now - timedelta(hours=1))) == 2
    [session] = sidebar.scan_sessions(hours_back=1, agent_ids={"agy"})
    assert (session.project, session.model, session.last_activity) == (entry.project, entry.model, entry.timestamp)
    assert [p.text for p in session.prompts] == ["Hello"]

    monkeypatch.setattr(cli, "_ensure_data_ready", lambda: pytest.fail("AGY-only reads must not install hooks"))
    monkeypatch.setattr(sys, "argv", ["tt", "sidebar", "--agy", "--once"])
    cli.main()
    assert "sample repo" in capsys.readouterr().out


def test_agy_scoped_setup_preserves_and_restores_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agy, "AGY_HOME", tmp_path)
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path / "tt"))
    monkeypatch.setattr(cli, "_run_setup_flow", lambda: pytest.fail("Scoped setup touched other agents"))
    monkeypatch.setattr(cli, "unsetup", lambda: pytest.fail("Scoped unsetup touched other agents"))
    path = tmp_path / "settings.json"
    before = {"statusLine": {"type": "", "command": "", "enabled": False}, "theme": "light"}
    path.write_text(json.dumps(before), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tt", "setup", "--agy"])
    cli.main()
    configured = json.loads(path.read_text(encoding="utf-8"))
    assert configured["theme"] == "light" and hooks.agy_statusline_active()
    assert configured["statusLine"]["enabled"] is True
    assert configured["statusLine"]["stack_with_default"] is True
    assert configured["statusLine"]["command"].endswith(" -B -m token_tracker.templates.agy_statusline")
    cli.main()
    assert json.loads(path.read_text(encoding="utf-8")) == configured
    capsys.readouterr()

    configured["theme"] = "dark"
    path.write_text(json.dumps(configured), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["tt", "unsetup", "--agy"])
    cli.main()
    assert json.loads(path.read_text(encoding="utf-8")) == {**before, "theme": "dark"}
    assert not hooks.agy_statusline_active()

    custom = '{"statusLine":{"type":"command","command":"/custom/statusline"}}'
    for original in (custom, "malformed json"):
        path.write_text(original, encoding="utf-8")
        assert not hooks.configure_agy_statusline(True, quiet=True)
        assert not hooks.configure_agy_statusline(False, quiet=True)
        assert path.read_text(encoding="utf-8") == original


def test_agy_setup_wizard_offers_statusline(tmp_path, monkeypatch):
    from token_tracker import wizard

    monkeypatch.setattr(agy, "AGY_HOME", tmp_path)
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path / "tt"))
    monkeypatch.setattr(wizard, "_has_cc", lambda: False)
    monkeypatch.setattr(wizard, "_has_codex", lambda: False)
    monkeypatch.setattr(wizard, "_has_kimi", lambda: False)
    asked = []
    monkeypatch.setattr(wizard, "_ask_yes_no", lambda label, default: asked.append((label, default)) or True)
    result = wizard.ask_components(step_prefix_fn=lambda i: f"[{i}] ")
    assert result.agy_statusline is True
    assert len(asked) == 1 and asked[0][0].startswith("[1] ") and asked[0][1] is False


def test_agy_statusline_and_quota_snapshot(tmp_path, monkeypatch, capsys):
    quota_file = tmp_path / "tt-agy-quota.json"
    monkeypatch.setattr(agy_quota, "QUOTA_FILE", quota_file)
    now = datetime.now(UTC)
    reset = (now + timedelta(hours=2)).isoformat()
    payload = {
        "email": "email@example.com", "model": {"display_name": "Gemini"},
        "context_window": {"total_input_tokens": 601, "total_output_tokens": 50, "used_percentage": 4.2},
        "quota": {
            "gemini-5h": {"remaining_fraction": .75, "reset_time": reset},
            "gemini-weekly": {"remaining_fraction": .5, "reset_time": reset},
            "3p-5h": {"remaining_fraction": 1, "reset_time": "1970-01-01T00:00:00Z"},
            "3p-weekly": {"remaining_fraction": .6, "reset_time": (now - timedelta(seconds=1)).isoformat()},
            "other-weekly": {"remaining_fraction": .1, "reset_time": reset},
        },
    }
    monkeypatch.setattr(sys, "argv", ["tt", "agy-statusline"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    cli.main()
    output = capsys.readouterr().out
    assert "In: 601 · Out: 50 | Ctx: 4.2%" in output
    assert "Gemini used: 5h 25.0% (reset " in output
    assert "7d 50.0%" in output
    assert output.splitlines()[-1] == "Claude / GPT used: 5h 0.0%"
    saved = json.loads(quota_file.read_text())
    assert set(saved) == {"gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly"}
    assert "email@example.com" not in quota_file.read_text()
    assert agy_quota.load_rate_limits()["Gemini"].five_hour_pct == 25
    monkeypatch.setattr(agy, "AGY_HOME", tmp_path)
    monkeypatch.setattr(sys, "argv", ["tt", "status", "--agy"])
    cli.main()
    panel = Text.from_ansi(capsys.readouterr().out).plain
    assert "Antigravity CLI · Gemini" in panel and "25.0%" in panel
    expired = now.timestamp() - agy_quota.SNAPSHOT_MAX_AGE - 1
    os.utime(quota_file, (expired, expired))
    assert agy_quota.load_rate_limits() == {}
    assert agy_statusline.render({"quota": {"gemini-5h": {"remaining_fraction": True}}}, now) == ""
