from collections import defaultdict
from datetime import timedelta

from ..adapters.types import DailyStats, MonthlyStats, SessionStats, UsageEntry, WeeklyStats
from .cost import calculate_cost


def _accumulate(stat, entry: UsageEntry) -> None:
    cost = calculate_cost(entry)
    stat.input_tokens += entry.input_tokens
    stat.output_tokens += entry.output_tokens
    stat.cache_creation_tokens += entry.cache_creation_tokens
    stat.cache_read_tokens += entry.cache_read_tokens
    stat.total_tokens += entry.total_tokens
    stat.cost_usd += cost
    stat.message_count += entry.message_count
    if hasattr(stat, "models"):
        stat.models[entry.model] = stat.models.get(entry.model, 0) + entry.total_tokens


def aggregate_daily(entries: list[UsageEntry]) -> list[DailyStats]:
    by_date: dict[str, DailyStats] = {}
    sessions: dict[str, set[str]] = defaultdict(set)

    for e in entries:
        key = e.timestamp.strftime("%Y-%m-%d")
        if key not in by_date:
            by_date[key] = DailyStats(date=key)
        _accumulate(by_date[key], e)
        sessions[key].add(e.session_id)

    for key, sids in sessions.items():
        by_date[key].session_count = len(sids)

    return sorted(by_date.values(), key=lambda s: s.date)


def aggregate_monthly(entries: list[UsageEntry]) -> list[MonthlyStats]:
    by_month: dict[str, MonthlyStats] = {}
    sessions: dict[str, set[str]] = defaultdict(set)

    for e in entries:
        key = e.timestamp.strftime("%Y-%m")
        if key not in by_month:
            by_month[key] = MonthlyStats(month=key)
        _accumulate(by_month[key], e)
        sessions[key].add(e.session_id)

    for key, sids in sessions.items():
        by_month[key].session_count = len(sids)

    return sorted(by_month.values(), key=lambda s: s.month)


def aggregate_weekly(entries: list[UsageEntry]) -> list[WeeklyStats]:
    by_week: dict[str, WeeklyStats] = {}
    sessions: dict[str, set[str]] = defaultdict(set)

    for e in entries:
        monday = e.timestamp.date() - timedelta(days=e.timestamp.weekday())
        sunday = monday + timedelta(days=6)
        key = monday.isoformat()
        if key not in by_week:
            by_week[key] = WeeklyStats(
                week=key,
                week_start=monday.strftime("%m-%d"),
                week_end=sunday.strftime("%m-%d"),
            )
        _accumulate(by_week[key], e)
        sessions[key].add(e.session_id)

    for key, sids in sessions.items():
        by_week[key].session_count = len(sids)

    return sorted(by_week.values(), key=lambda s: s.week)


def aggregate_sessions(entries: list[UsageEntry]) -> list[SessionStats]:
    by_session: dict[str, list[UsageEntry]] = defaultdict(list)
    for e in entries:
        by_session[e.session_id].append(e)

    sessions: list[SessionStats] = []
    for session_id, session_entries in by_session.items():
        session_entries.sort(key=lambda e: e.timestamp)
        first = session_entries[0]
        last = session_entries[-1]
        duration = (last.timestamp - first.timestamp).total_seconds() / 60

        models: dict[str, int] = defaultdict(int)
        for e in session_entries:
            models[e.model] += e.total_tokens
        primary_model = max(models, key=models.get) if models else "unknown"

        s = SessionStats(
            session_id=session_id,
            project=first.project,
            model=primary_model,
            start_time=first.timestamp,
            end_time=last.timestamp,
            duration_minutes=round(duration, 1),
        )
        for e in session_entries:
            _accumulate(s, e)
        sessions.append(s)

    sessions.sort(key=lambda s: s.start_time, reverse=True)
    return sessions
