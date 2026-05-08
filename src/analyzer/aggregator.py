from collections import defaultdict
from datetime import datetime

from ..adapters.types import DailyStats, MonthlyStats, SessionStats, UsageEntry
from .cost import calculate_cost


def aggregate_daily(entries: list[UsageEntry]) -> list[DailyStats]:
    by_date: dict[str, DailyStats] = {}
    sessions_by_date: dict[str, set[str]] = defaultdict(set)

    for e in entries:
        date_str = e.timestamp.strftime("%Y-%m-%d")
        if date_str not in by_date:
            by_date[date_str] = DailyStats(date=date_str)
        s = by_date[date_str]
        cost = calculate_cost(e)
        s.input_tokens += e.input_tokens
        s.output_tokens += e.output_tokens
        s.cache_creation_tokens += e.cache_creation_tokens
        s.cache_read_tokens += e.cache_read_tokens
        s.total_tokens += e.total_tokens
        s.cost_usd += cost
        s.message_count += 1
        s.models[e.model] = s.models.get(e.model, 0) + e.total_tokens
        sessions_by_date[date_str].add(e.session_id)

    for date_str, sessions in sessions_by_date.items():
        by_date[date_str].session_count = len(sessions)

    return sorted(by_date.values(), key=lambda s: s.date)


def aggregate_monthly(entries: list[UsageEntry]) -> list[MonthlyStats]:
    by_month: dict[str, MonthlyStats] = {}
    sessions_by_month: dict[str, set[str]] = defaultdict(set)

    for e in entries:
        month_str = e.timestamp.strftime("%Y-%m")
        if month_str not in by_month:
            by_month[month_str] = MonthlyStats(month=month_str)
        s = by_month[month_str]
        cost = calculate_cost(e)
        s.input_tokens += e.input_tokens
        s.output_tokens += e.output_tokens
        s.cache_creation_tokens += e.cache_creation_tokens
        s.cache_read_tokens += e.cache_read_tokens
        s.total_tokens += e.total_tokens
        s.cost_usd += cost
        s.message_count += 1
        s.models[e.model] = s.models.get(e.model, 0) + e.total_tokens
        sessions_by_month[month_str].add(e.session_id)

    for month_str, sessions in sessions_by_month.items():
        by_month[month_str].session_count = len(sessions)

    return sorted(by_month.values(), key=lambda s: s.month)


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
            cost = calculate_cost(e)
            s.input_tokens += e.input_tokens
            s.output_tokens += e.output_tokens
            s.cache_creation_tokens += e.cache_creation_tokens
            s.cache_read_tokens += e.cache_read_tokens
            s.total_tokens += e.total_tokens
            s.cost_usd += cost
            s.message_count += 1

        sessions.append(s)

    sessions.sort(key=lambda s: s.start_time, reverse=True)
    return sessions
