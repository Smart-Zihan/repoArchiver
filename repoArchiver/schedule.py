"""Scheduler snippets for periodic syncs, and the on-launch trace throttle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _minutes(interval_seconds: float) -> int:
    return max(1, int(round(interval_seconds / 60.0)))


def _cron(interval_seconds: float) -> str:
    minutes = _minutes(interval_seconds)
    if minutes < 60:
        return f"*/{minutes} * * * *"
    hours = minutes // 60
    if minutes % 60 == 0 and hours < 24:
        return f"0 */{hours} * * *" if 24 % hours == 0 else "0 0 * * *"
    days = max(1, hours // 24)
    if minutes % 1440 == 0:
        return f"0 0 */{days} * *"
    return "0 0 * * *"


def _schtasks(interval_seconds: float) -> str:
    """The /SC ... /MO ... half of a schtasks command."""

    minutes = _minutes(interval_seconds)
    if minutes < 60:
        return f"MINUTE /MO {minutes}"
    hours = minutes // 60
    if hours < 24:
        return f"HOURLY /MO {hours}"
    return f"DAILY /MO {max(1, hours // 24)}"


def _systemd(interval_seconds: float) -> str:
    minutes = _minutes(interval_seconds)
    if minutes % 60 == 0 and minutes < 1440:
        return f"OnUnitActiveSec={minutes // 60}h"
    if minutes % 1440 == 0:
        return f"OnUnitActiveSec={minutes // 1440}d"
    return f"OnUnitActiveSec={minutes}min"


@dataclass(frozen=True)
class SchedulePlan:
    """Scheduler snippets for one interval."""

    interval_seconds: float
    command: str
    cron: str
    schtasks: str
    systemd: str

    def to_json(self) -> dict[str, Any]:
        return {
            "interval_seconds": self.interval_seconds,
            "command": self.command,
            "cron": self.cron,
            "schtasks": self.schtasks,
            "systemd": self.systemd,
        }


def plan(
    interval_seconds: float,
    *,
    archives_dir: str | Path | None = None,
    archive_ids: tuple[str, ...] = (),
    executable: str = "repoArchiver",
) -> SchedulePlan:
    """Build the scheduler snippets for interval_seconds."""

    sync_args = " ".join(archive_ids)
    command = f"{executable} sync {sync_args}".strip()
    directory = Path(archives_dir).as_posix() if archives_dir is not None else ""
    if directory:
        cron = f"{_cron(interval_seconds)} cd {directory} && {command}"
        schtasks_cmd = f"cmd /c cd /d {directory} && {command}"
    else:
        cron = f"{_cron(interval_seconds)} {command}"
        schtasks_cmd = command
    return SchedulePlan(
        interval_seconds=float(interval_seconds),
        command=command,
        cron=cron,
        schtasks=(
            f'schtasks /Create /SC {_schtasks(interval_seconds)} '
            f'/TN repoArchiver /TR "{schtasks_cmd}" /F'
        ),
        systemd=_systemd(interval_seconds),
    )


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def should_trace(state: dict[str, Any] | None, interval_seconds: float) -> bool:
    """Whether an on-launch trace is due, given the last sync time."""

    if interval_seconds <= 0:
        return True
    if not state:
        return True
    last = _parse_iso(state.get("last_sync_at")) or _parse_iso(state.get("last_trace_at"))
    if last is None:
        return True
    age = (datetime.now(UTC) - last).total_seconds()
    return age >= interval_seconds



__all__ = ["SchedulePlan", "plan", "should_trace"]
