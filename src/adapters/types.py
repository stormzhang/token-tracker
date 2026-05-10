from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UsageEntry:
    timestamp: datetime
    session_id: str
    message_id: str
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float | None
    project: str
    agent_id: str
    message_count: int = 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens + self.cache_read_tokens

    @property
    def dedup_key(self) -> str:
        return f"{self.message_id}:{self.request_id}"


@dataclass
class AgentInfo:
    id: str
    name: str
    data_dir: str
    installed: bool


@dataclass
class DailyStats:
    date: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    message_count: int = 0
    models: dict[str, int] = field(default_factory=dict)
    agent_id: str = ""


@dataclass
class WeeklyStats:
    week: str
    week_start: str = ""
    week_end: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    message_count: int = 0
    models: dict[str, int] = field(default_factory=dict)
    agent_id: str = ""


@dataclass
class SessionStats:
    session_id: str
    project: str
    model: str
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    message_count: int = 0
    agent_id: str = ""


@dataclass
class MonthlyStats:
    month: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    message_count: int = 0
    models: dict[str, int] = field(default_factory=dict)
    agent_id: str = ""


@dataclass
class RateLimits:
    five_hour_pct: float | None = None
    five_hour_resets_at: int | None = None
    seven_day_pct: float | None = None
    seven_day_resets_at: int | None = None
    model: str = ""
    updated_at: str = ""


@dataclass
class P90Limits:
    token_limit: int = 0
    cost_limit: float = 0.0
    message_limit: int = 0


@dataclass
class SessionBlock:
    start_time: datetime
    end_time: datetime
    entries: list[UsageEntry] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    is_active: bool = False
    burn_rate: float = 0.0
    is_gap: bool = False
