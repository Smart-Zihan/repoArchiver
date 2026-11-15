"""HTTP timeouts, retries, rate-limit handling and resumable downloads."""

from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .errors import RepoArchiverError, warn

USER_AGENT = f"repoArchiver/{__version__}"
DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.0
MAX_BACKOFF = 30.0
MAX_RATE_LIMIT_WAIT = 300.0
CHUNK_SIZE = 256 * 1024
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError)


class HttpError(RepoArchiverError):
    """An HTTP request failed for good (or was retried to exhaustion)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        url: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.retryable = retryable

    @property
    def not_found(self) -> bool:
        return self.status == 404

    @property
    def unauthorized(self) -> bool:
        return self.status in (401, 403)


@dataclass(frozen=True)
class HttpOptions:
    """Knobs for one HTTP session (built from the config file)."""

    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    backoff: float = DEFAULT_BACKOFF
    min_interval: float = 0.0
    resume: bool = True

    @classmethod
    def from_config(cls, config: object | None) -> HttpOptions:
        """Build options from a Config-like object."""

        if config is None:
            return cls()
        timeout = getattr(config, "timeout", None)
        retries = getattr(config, "retries", None)
        min_interval = getattr(config, "min_interval", None)
        resume = getattr(config, "resume", None)
        return cls(
            timeout=float(timeout) if isinstance(timeout, (int, float)) else DEFAULT_TIMEOUT,
            retries=int(retries) if isinstance(retries, int) else DEFAULT_RETRIES,
            min_interval=(
                float(min_interval) if isinstance(min_interval, (int, float)) else 0.0
            ),
            resume=resume if isinstance(resume, bool) else True,
        )

    def tightened(self, **changes: Any) -> HttpOptions:
        """Return a copy with some fields replaced."""

        return replace(self, **changes)


# host -> monotonic timestamp of the last request
_LAST_REQUEST: dict[str, float] = {}


def reset_throttle() -> None:
    """Forget the per-host request clock."""

    _LAST_REQUEST.clear()


def _throttle(host: str, options: HttpOptions) -> None:
    if options.min_interval <= 0 or not host:
        return
    now = time.monotonic()
    wait = _LAST_REQUEST.get(host, 0.0) + options.min_interval - now
    if wait > 0:
        time.sleep(min(wait, options.min_interval))
    _LAST_REQUEST[host] = time.monotonic()


def _open(request: urllib.request.Request, options: HttpOptions) -> Any:
    return urllib.request.urlopen(request, timeout=options.timeout)


def _delay(attempt: int, options: HttpOptions, retry_after: float | None = None) -> float:
    if retry_after is not None and retry_after > 0:
        return min(retry_after, MAX_BACKOFF * 4)
    base = options.backoff * (2**attempt)
    return min(base + random.uniform(0, options.backoff), MAX_BACKOFF)


def _header_float(headers: Any, name: str) -> float | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _retry_after(headers: Any) -> float | None:
    """Seconds the server asked us to wait (Retry-After or rate-limit reset)."""

    direct = _header_float(headers, "Retry-After")
    if direct is not None:
        return direct
    remaining = _header_float(headers, "X-RateLimit-Remaining")
    reset = _header_float(headers, "X-RateLimit-Reset")
    if remaining is not None and remaining <= 0 and reset is not None:
        return max(0.0, reset - time.time())
    return None


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - the body is best-effort detail only
        return ""
    finally:
        try:
            exc.close()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(payload, dict):
        for key in ("message", "error", "error_description", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _is_rate_limited(exc: urllib.error.HTTPError) -> bool:
    remaining = _header_float(exc.headers, "X-RateLimit-Remaining")
    return exc.code == 429 or (exc.code == 403 and remaining is not None and remaining <= 0)


def request_bytes(
    url: str,
    headers: dict[str, str] | None = None,
    options: HttpOptions | None = None,
) -> tuple[bytes, dict[str, str]]:
    """GET url and return (body, headers)."""

    opts = options or HttpOptions()
    all_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    all_headers.update(headers or {})
    host = urlsplit(url).hostname or ""
    last: HttpError | None = None

    for attempt in range(opts.retries + 1):
        _throttle(host, opts)
        request = urllib.request.Request(url, headers=all_headers)
        try:
            with _open(request, opts) as response:
                body = response.read()
                return body, {str(key): str(value) for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            status = exc.code
            detail = _read_error(exc)
            suffix = f": {detail}" if detail else ""
            if status in RETRY_STATUS or _is_rate_limited(exc):
                wait = _retry_after(exc.headers)
                if wait is not None and wait > MAX_RATE_LIMIT_WAIT:
                    raise HttpError(
                        f"rate limited by {host or 'the server'} for another {int(wait)}s "
                        f"(HTTP {status}); configure a token in the config file to raise "
                        "the limit",
                        status=status,
                        url=url,
                    ) from exc
                last = HttpError(
                    f"HTTP {status}{suffix}", status=status, url=url, retryable=True
                )
                if attempt < opts.retries:
                    time.sleep(_delay(attempt, opts, wait))
                    continue
                raise last from exc
            raise HttpError(f"HTTP {status}{suffix}", status=status, url=url) from exc
        except _NETWORK_ERRORS as exc:
            last = HttpError(
                f"cannot reach {host or url} ({exc})", url=url, retryable=True
            )
            if attempt < opts.retries:
                time.sleep(_delay(attempt, opts))
                continue
            raise last from exc

    raise last or HttpError(f"request to {url} failed", url=url)


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    options: HttpOptions | None = None,
) -> tuple[Any, dict[str, str]]:
    """GET url and decode JSON; raises HttpError on garbage."""

    body, response_headers = request_bytes(url, headers, options)
    try:
        return json.loads(body.decode("utf-8")), response_headers
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(f"invalid JSON response from {url} ({exc})", url=url) from exc


def download(
    url: str,
    target: str | os.PathLike[str],
    headers: dict[str, str] | None = None,
    options: HttpOptions | None = None,
    expected_size: int | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Download url to target, resuming a previous partial download."""

    opts = options or HttpOptions()
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = target_path.with_name(f".{target_path.name}.part")

    if target_path.is_file() and (
        expected_size is None or target_path.stat().st_size == expected_size
    ):
        return target_path

    all_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    all_headers.update(headers or {})
    host = urlsplit(url).hostname or ""
    last_exc: Exception | None = None

    for attempt in range(opts.retries + 1):
        have = part_path.stat().st_size if part_path.is_file() else 0
        if expected_size is not None and have == expected_size and have > 0:
            os.replace(part_path, target_path)
            return target_path
        if expected_size is not None and have > expected_size:
            part_path.unlink(missing_ok=True)
            have = 0

        send_headers = dict(all_headers)
        if opts.resume and have > 0:
            send_headers["Range"] = f"bytes={have}-"

        _throttle(host, opts)
        request = urllib.request.Request(url, headers=send_headers)
        try:
            with _open(request, opts) as response:
                status = getattr(response, "status", 200) or 200
                mode = "ab" if (status == 206 and have > 0) else "wb"
                written = have if mode == "ab" else 0
                with part_path.open(mode) as handle:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if on_progress is not None:
                            try:
                                on_progress(written, expected_size)
                            except Exception:  # noqa: BLE001 - display must not fail a download
                                on_progress = None
                if expected_size is not None and written != expected_size:
                    raise HttpError(
                        f"size mismatch for {target_path.name} "
                        f"(expected {expected_size} bytes, got {written})",
                        url=url,
                        retryable=True,
                    )
            os.replace(part_path, target_path)
            return target_path
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have > 0:
                part_path.unlink(missing_ok=True)
                last_exc = HttpError("range not satisfiable", status=416, url=url)
                if attempt < opts.retries:
                    time.sleep(_delay(attempt, opts))
                    continue
                raise last_exc from exc
            detail = _read_error(exc)
            suffix = f": {detail}" if detail else ""
            if exc.code in RETRY_STATUS or _is_rate_limited(exc):
                wait = _retry_after(exc.headers)
                if wait is not None and wait > MAX_RATE_LIMIT_WAIT:
                    raise HttpError(
                        f"rate limited while downloading {target_path.name} for another "
                        f"{int(wait)}s ({suffix.strip(': ') or 'HTTP ' + str(exc.code)})",
                        status=exc.code,
                        url=url,
                    ) from exc
                last_exc = HttpError(f"HTTP {exc.code}{suffix}", status=exc.code, url=url)
                if attempt < opts.retries:
                    time.sleep(_delay(attempt, opts, wait))
                    continue
                raise last_exc from exc
            part_path.unlink(missing_ok=True)
            raise HttpError(f"HTTP {exc.code}{suffix}", status=exc.code, url=url) from exc
        except _NETWORK_ERRORS as exc:
            last_exc = HttpError(f"cannot reach {host or url} ({exc})", url=url)
            if attempt < opts.retries:
                time.sleep(_delay(attempt, opts))
                continue
            if not opts.resume:
                part_path.unlink(missing_ok=True)
            raise last_exc from exc
        except HttpError as exc:
            if attempt < opts.retries and exc.retryable:
                last_exc = exc
                time.sleep(_delay(attempt, opts))
                continue
            raise

    raise last_exc if isinstance(last_exc, RepoArchiverError) else HttpError(
        f"download of {url} failed", url=url
    )


def leftovers(directory: str | os.PathLike[str]) -> list[Path]:
    """Return partial-download files left in directory."""

    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob(".*.part") if path.is_file())



__all__ = [
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "HttpError",
    "HttpOptions",
    "download",
    "get_json",
    "leftovers",
    "request_bytes",
    "reset_throttle",
    "warn",
]
