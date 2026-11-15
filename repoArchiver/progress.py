"""Live progress output for the waiting parts of an archive run."""

from __future__ import annotations

import contextlib
import re
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, TextIO

from .i18n import tr

AUTO = "auto"
ALWAYS = "always"
NEVER = "never"
MODES = (AUTO, ALWAYS, NEVER)

HEARTBEAT_SECONDS = 5.0
MILESTONE_STEP = 10  # percentage points between plain-mode git milestones
_BAR_WIDTH = 20

_PERCENT = re.compile(r"(\d{1,3})%")
_TRAILING_NUMBER = re.compile(r"[\s:,]*\d+\s*$")
_REMOTE_PREFIX = re.compile(r"^remote:\s*")


def format_size(size: int) -> str:
    """Human-readable byte count (1.2 MiB)."""

    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def parse_git_progress(line: str) -> tuple[str, int] | None:
    """Split a git progress line into (phase, percent)."""

    text = line.strip()
    if not text:
        return None
    match = _PERCENT.search(text)
    if match is None:
        return None
    try:
        percent = int(match.group(1))
    except ValueError:  # pragma: no cover - regex guarantees digits
        return None
    head = _TRAILING_NUMBER.sub("", text[: match.start()]).strip()
    head = _REMOTE_PREFIX.sub("", head).rstrip(": ").strip()
    return (head or "git"), max(0, min(100, percent))


class Reporter:
    """Renders progress for one run."""

    def __init__(
        self,
        mode: str = AUTO,
        stream: TextIO | None = None,
        *,
        verbose: bool = False,
        heartbeat: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.mode = mode if mode in MODES else AUTO
        self.stream = stream
        self.verbose = verbose
        self.heartbeat = heartbeat
        self._lock = threading.Lock()
        self._last_write = time.monotonic()
        self._line_open = False
        self._width = 0
        self._milestones: dict[str, int] = {}
        self._milestone_times: dict[str, float] = {}
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._depth = 0

    @property
    def out(self) -> TextIO:
        return self.stream if self.stream is not None else sys.stderr

    @property
    def interactive(self) -> bool:
        """Whether we may redraw a single line with carriage returns."""

        if self.mode == NEVER:
            return False
        try:
            return bool(self.out.isatty())
        except (AttributeError, ValueError):
            return False

    @property
    def enabled(self) -> bool:
        return self.mode == ALWAYS or self.interactive

    def _write(self, text: str) -> None:
        try:
            with self._lock:
                self.out.write(text)
                self.out.flush()
                self._last_write = time.monotonic()
        except (ValueError, OSError):
            pass

    def _finish_line(self) -> None:
        if self._line_open:
            self._line_open = False
            self._width = 0
            self._write("\n")

    def _live(self, text: str) -> None:
        """Redraw the current line (terminal only)."""

        width = max(len(text), self._width)
        self._width = width
        self._line_open = True
        self._write("\r" + text + " " * (width - len(text)))

    def _bar(self, label: str, percent: int, suffix: str = "") -> None:
        filled = max(0, min(_BAR_WIDTH, percent * _BAR_WIDTH // 100))
        bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
        self._live(f"  {label} [{bar}] {percent:3d}%{suffix}")

    def stage(self, message: str) -> None:
        """Announce the step that is about to run."""

        if not self.enabled:
            return
        self._finish_line()
        self._write(f"→ {message}\n")

    def note(self, message: str) -> None:
        """Print one detail line under the current stage."""

        if not self.enabled:
            return
        self._finish_line()
        self._write(f"  {message}\n")

    @contextlib.contextmanager
    def long_stage(self, message: str) -> Iterator[None]:
        """Announce a slow step and keep a heartbeat running inside it."""

        self.stage(message)
        self._start_heartbeat()
        try:
            yield
        finally:
            self._stop_heartbeat()
            self._finish_line()

    def git(self, line: str) -> None:
        """Consume one line of git's --progress output."""

        if not self.enabled:
            return
        text = line.strip()
        if not text:
            return
        if self.verbose:
            self.note(text)
            return
        parsed = parse_git_progress(text)
        if parsed is None:
            return
        phase, percent = parsed
        if not self.interactive:
            self._milestone(phase, percent)
            return
        self._bar(phase, percent)

    def _milestone(self, phase: str, percent: int) -> None:
        """Print coarse progress when no bar can be drawn."""

        previous = self._milestones.get(phase, -1)
        if percent >= 100:
            if previous >= 100:
                return
            self._milestones[phase] = 100
        else:
            if previous < 0:
                self._milestones[phase] = 0
                self._milestone_times[phase] = time.monotonic()
                return
            if percent - previous < MILESTONE_STEP:
                return
            now = time.monotonic()
            if now - self._milestone_times.get(phase, 0.0) < 1.0:
                return
            self._milestones[phase] = percent - percent % MILESTONE_STEP
            self._milestone_times[phase] = now
        self._finish_line()
        self._write(f"  {phase} {percent}%\n")

    def download(self, label: str, done: int, total: int | None) -> None:
        """Redraw the download bar of label (terminal only)."""

        if not self.enabled or not self.interactive:
            return
        if total and total > 0:
            percent = max(0, min(100, done * 100 // total))
            self._bar(label, percent, f"  {format_size(done)}/{format_size(total)}")
        else:
            self._live(f"  {label}  {format_size(done)}")

    def downloaded(self, name: str, size: int) -> None:
        """Record a finished download as a normal line."""

        if not self.enabled:
            return
        self._finish_line()
        self._write(f"  {tr('downloaded {name} ({size} bytes)', name=name, size=size)}\n")

    def break_line(self) -> None:
        """End the current bar line so ordinary output can follow it."""

        self._finish_line()

    def close(self) -> None:
        """Stop the heartbeat and finish any open line."""

        self._stop_heartbeat()
        self._finish_line()

    def _start_heartbeat(self) -> None:
        self._depth += 1
        if self._depth > 1 or not self.interactive or self._thread is not None:
            return
        self._last_write = time.monotonic()
        stop = threading.Event()
        self._stop_event = stop

        def beat() -> None:
            started = time.monotonic()
            while not stop.wait(self.heartbeat):
                if time.monotonic() - self._last_write < self.heartbeat:
                    continue
                elapsed = int(time.monotonic() - started)
                self._live("  " + tr("… still working ({seconds}s)", seconds=elapsed))

        thread = threading.Thread(target=beat, name="repoArchiver-progress", daemon=True)
        self._thread = thread
        thread.start()

    def _stop_heartbeat(self) -> None:
        if self._depth > 0:
            self._depth -= 1
        if self._depth > 0:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._stop_event = None


_ACTIVE: Reporter | None = None


def configure(
    mode: str = AUTO,
    stream: TextIO | None = None,
    *,
    verbose: bool = False,
) -> Reporter:
    """Create the process-wide reporter used by the pipeline."""

    global _ACTIVE
    _ACTIVE = Reporter(mode, stream, verbose=verbose)
    return _ACTIVE


def reset() -> None:
    """Forget the active reporter."""

    global _ACTIVE
    if _ACTIVE is not None:
        _ACTIVE.close()
    _ACTIVE = None


def progress() -> Reporter | None:
    """Return the active reporter, or None when nothing is configured."""

    return _ACTIVE


def active() -> bool:
    """Whether progress output is currently visible."""

    return _ACTIVE is not None and _ACTIVE.enabled


def stage(message: str) -> None:
    """Shorthand: announce a stage on the active reporter."""

    if _ACTIVE is not None:
        _ACTIVE.stage(message)


@contextlib.contextmanager
def long_stage(message: str) -> Iterator[None]:
    """Shorthand: announce a slow stage and keep the heartbeat alive."""

    if _ACTIVE is None:
        yield
        return
    with _ACTIVE.long_stage(message):
        yield


def git_line(line: str) -> None:
    """Shorthand: feed one git progress line to the active reporter."""

    if _ACTIVE is not None:
        _ACTIVE.git(line)


def normalize_mode(value: Any) -> str:
    """Map a CLI/config value to one of MODES."""

    if isinstance(value, str):
        text = value.strip().lower()
        if text in MODES:
            return text
    return AUTO


def stream_is_tty(stream: TextIO | None = None) -> bool:
    """isatty that never raises."""

    target = stream if stream is not None else sys.stderr
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError, OSError):
        return False


__all__ = [
    "ALWAYS",
    "AUTO",
    "MODES",
    "NEVER",
    "Reporter",
    "active",
    "configure",
    "format_size",
    "git_line",
    "long_stage",
    "normalize_mode",
    "parse_git_progress",
    "progress",
    "reset",
    "stage",
    "stream_is_tty",
]
