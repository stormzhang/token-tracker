"""Normalize and cache the quota buckets supplied by Antigravity CLI."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .agy import parse_timestamp
from .types import RateLimits

QUOTA_FILE = Path.home() / ".config" / "token-tracker" / "tt-agy-quota.json"
SNAPSHOT_MAX_AGE = 15 * 60
QUOTA_GROUPS = {"gemini": "Gemini", "3p": "Claude / GPT"}


def known_buckets(quota: object) -> dict:
    if not isinstance(quota, dict):
        return {}
    buckets = {}
    for group in QUOTA_GROUPS:
        for window in ("5h", "weekly"):
            key = f"{group}-{window}"
            bucket = quota.get(key)
            if not isinstance(bucket, dict) or bucket.get("disabled"):
                continue
            fraction = bucket.get("remaining_fraction")
            if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not 0 <= fraction <= 1:
                continue
            resets = parse_timestamp(bucket.get("reset_time"))
            # AGY uses the Unix epoch for a full bucket whose window hasn't started.
            reset_time = resets.isoformat() if resets and resets.timestamp() > 0 else None
            buckets[key] = {"remaining_fraction": fraction, "reset_time": reset_time}
    return buckets


def quota_groups(buckets: dict, now: datetime) -> dict[str, RateLimits]:
    def window(key: str) -> tuple[float | None, int | None]:
        bucket = buckets.get(key)
        if bucket is None:
            return None, None
        resets = parse_timestamp(bucket["reset_time"])
        if resets is not None and resets <= now:
            return None, None
        return (1 - bucket["remaining_fraction"]) * 100, int(resets.timestamp()) if resets else None

    groups = {}
    for group, label in QUOTA_GROUPS.items():
        five, five_reset = window(f"{group}-5h")
        week, week_reset = window(f"{group}-weekly")
        if five is not None or week is not None:
            groups[label] = RateLimits(
                five_hour_pct=five, five_hour_resets_at=five_reset,
                seven_day_pct=week, seven_day_resets_at=week_reset,
            )
    return groups


def save_snapshot(buckets: dict) -> None:
    if not buckets:
        return
    temporary = None
    try:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=QUOTA_FILE.parent, prefix=".agy-quota-", mode="w", encoding="utf-8", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(buckets, stream)
        temporary.replace(QUOTA_FILE)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_rate_limits() -> dict[str, RateLimits]:
    now = datetime.now(UTC)
    try:
        with QUOTA_FILE.open(encoding="utf-8") as stream:
            age = now.timestamp() - os.fstat(stream.fileno()).st_mtime
            if not 0 <= age <= SNAPSHOT_MAX_AGE:
                return {}
            buckets = known_buckets(json.load(stream))
    except (OSError, ValueError):
        return {}
    return quota_groups(buckets, now)
