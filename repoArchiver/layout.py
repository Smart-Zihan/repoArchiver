"""Archive layout: source parsing, config loading, and JSON metadata I/O."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .errors import ArchiveError, ConfigError
from .i18n import normalize_language

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only for old interpreters
    tomllib = None  # type: ignore[assignment]

DEFAULT_ARCHIVES_DIR = "archives"
CONFIG_DIRNAME = ".repoArchiver"
CONFIG_FILENAME = "config.toml"
HISTORY_FILENAME = "history.jsonl"

_SAFE_COMPONENT = re.compile(r"[^\w.-]+", re.UNICODE)
_SCP_STYLE = re.compile(r"^(?:(?P<user>[^@/\\]+)@)?(?P<host>[^:/\\]+):(?P<path>.+)$")


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    archives_dir: Path
    token: str | None = None
    trace: bool = True
    path: Path | None = None
    tokens: dict[str, str] = field(default_factory=dict)
    platform: str | None = None
    retries: int = 3
    timeout: float = 60.0
    min_interval: float = 0.0
    resume: bool = True
    trace_interval: float = 0.0
    language: str | None = None
    progress: str = "auto"

    def token_for(self, host: str) -> str | None:
        """Return the token to use for host."""

        host = (host or "").lower()
        if host and host in self.tokens:
            return self.tokens[host]
        if "*" in self.tokens:
            return self.tokens["*"]
        for name in _env_token_names(host):
            value = os.environ.get(name)
            if value and value.strip():
                return value.strip()
        if _is_github_host(host) or not host:
            return self.token
        return None


def _is_github_host(host: str) -> bool:
    return host in {"github.com", "www.github.com"} or host.startswith("github.")


def _env_token_names(host: str) -> tuple[str, ...]:
    """Environment variables that carry a token for host."""

    host = (host or "").lower()
    if "gitlab" in host:
        return ("GITLAB_TOKEN", "GITLAB_PRIVATE_TOKEN")
    if "bitbucket" in host:
        return ("BITBUCKET_TOKEN", "BITBUCKET_APP_PASSWORD")
    if "gitea" in host or "forgejo" in host or host == "codeberg.org":
        return ("GITEA_TOKEN", "CODEBERG_TOKEN")
    return ("GITHUB_TOKEN", "GH_TOKEN")


def parse_duration(value: object, *, key: str = "duration") -> float:
    """Parse 90, "90s", "30m", "6h" or "1d" into seconds."""

    if isinstance(value, bool):
        raise ConfigError(f"config key '{key}' must be a duration")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ConfigError(f"config key '{key}' must not be negative")
        return float(value)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config key '{key}' must be a duration like \"6h\"")
    text = value.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = text[-1]
    number = text[:-1] if suffix in units else text
    try:
        amount = float(number)
    except ValueError as exc:
        raise ConfigError(
            f'config key \'{key}\': cannot parse duration "{value}" (use e.g. "6h")'
        ) from exc
    if amount < 0:
        raise ConfigError(f"config key '{key}' must not be negative")
    return amount * units.get(suffix, 1)


def default_config_path() -> Path:
    """Return ~/.repoArchiver/config.toml."""

    return Path.home() / CONFIG_DIRNAME / CONFIG_FILENAME


def load_config(explicit: str | os.PathLike[str] | None = None) -> Config:
    """Load the TOML config."""

    path = Path(explicit).expanduser() if explicit is not None else default_config_path()
    if explicit is not None and not path.is_file():
        raise ConfigError(f'config file not found: "{path}"')

    data: dict[str, object] = {}
    if path.is_file():
        if tomllib is None:  # pragma: no cover
            raise ConfigError("reading TOML config requires Python 3.11 or newer")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f'cannot read config file "{path}": {exc}') from exc
        except UnicodeDecodeError as exc:
            raise ConfigError(f'config file "{path}" is not valid UTF-8') from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f'config file "{path}" is not valid TOML: {exc}') from exc

    raw_dir = data.get("archives_dir", DEFAULT_ARCHIVES_DIR)
    if not isinstance(raw_dir, str) or not raw_dir.strip():
        raise ConfigError("config key 'archives_dir' must be a non-empty string")
    archives_dir = Path(raw_dir).expanduser()
    if not archives_dir.is_absolute():
        archives_dir = Path.cwd() / archives_dir

    raw_token = data.get("token")
    if raw_token is not None and not isinstance(raw_token, str):
        raise ConfigError("config key 'token' must be a string")
    token = (raw_token or "").strip() or None
    if token is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None

    raw_trace = data.get("trace", True)
    if not isinstance(raw_trace, bool):
        raise ConfigError("config key 'trace' must be true or false")

    raw_tokens = data.get("tokens", {})
    if raw_tokens is None:
        raw_tokens = {}
    if not isinstance(raw_tokens, dict):
        raise ConfigError("config key 'tokens' must be a table of host -> token")
    tokens: dict[str, str] = {}
    for host, value in raw_tokens.items():
        if not isinstance(value, str):
            raise ConfigError(f"config key 'tokens.{host}' must be a string")
        if value.strip():
            tokens[str(host).lower()] = value.strip()

    raw_platform = data.get("platform")
    if raw_platform is not None and not isinstance(raw_platform, str):
        raise ConfigError("config key 'platform' must be a string")

    raw_retries = data.get("retries", 3)
    if not isinstance(raw_retries, int) or isinstance(raw_retries, bool) or raw_retries < 0:
        raise ConfigError("config key 'retries' must be a non-negative integer")

    raw_resume = data.get("resume", True)
    if not isinstance(raw_resume, bool):
        raise ConfigError("config key 'resume' must be true or false")

    timeout = data.get("timeout", 60.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ConfigError("config key 'timeout' must be a positive number of seconds")

    min_interval = parse_duration(data.get("min_interval", 0.0), key="min_interval")
    trace_interval = parse_duration(data.get("trace_interval", 0.0), key="trace_interval")

    raw_language = data.get("language")
    if raw_language is not None and not isinstance(raw_language, str):
        raise ConfigError("config key 'language' must be a string")
    language = normalize_language(raw_language)

    raw_progress = data.get("progress", "auto")
    if not isinstance(raw_progress, str):
        raise ConfigError("config key 'progress' must be one of: auto, always, never")
    progress_mode = raw_progress.strip().lower() or "auto"
    if progress_mode not in ("auto", "always", "never"):
        raise ConfigError("config key 'progress' must be one of: auto, always, never")

    return Config(
        archives_dir=archives_dir,
        token=token,
        trace=raw_trace,
        path=path if path.is_file() else None,
        tokens=tokens,
        platform=(raw_platform.strip().lower() if raw_platform and raw_platform.strip() else None),
        retries=raw_retries,
        timeout=float(timeout),
        min_interval=min_interval,
        resume=raw_resume,
        trace_interval=trace_interval,
        language=language,
        progress=progress_mode,
    )


def safe_component(value: str, fallback: str = "repo") -> str:
    """Reduce value to a single, filesystem-safe path component."""

    cleaned = _SAFE_COMPONENT.sub("_", value).strip("._")
    return cleaned or fallback


def _strip_git_suffix(name: str) -> str:
    return name[:-4] if name.lower().endswith(".git") and len(name) > 4 else name


def _local_source(path: Path) -> tuple[str, str, str]:
    resolved = path.expanduser().resolve()
    return "", resolved.parent.name or "local", _strip_git_suffix(resolved.name)


def parse_source(source: str) -> tuple[str, str, str]:
    """Split a repository source into (host, owner, repo)."""

    text = (source or "").strip()
    if not text:
        raise ConfigError("repository source must not be empty")

    if os.path.isdir(text):
        return _local_source(Path(text))

    windows_path = re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\")
    if windows_path:
        return _local_source(Path(text))

    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme == "file":
            return _local_source(Path(url2pathname(parts.path)))
        host = (parts.hostname or "").lower()
        raw_path = parts.path
    else:
        match = _SCP_STYLE.match(text)
        if match:
            host = match.group("host").lower()
            raw_path = match.group("path")
        else:
            return _local_source(Path(text))

    segments = [segment for segment in raw_path.replace("\\", "/").split("/") if segment]
    if len(segments) < 2:
        raise ConfigError(f'cannot parse a repository name from "{source}"')
    owner = segments[-2]
    repo = _strip_git_suffix(segments[-1])
    if not repo:
        raise ConfigError(f'cannot parse a repository name from "{source}"')
    return host, owner, repo


def archive_id_from_url(url: str) -> str:
    """Return the archive directory id, <owner>__<repo>."""

    _host, owner, repo = parse_source(url)
    return f"{safe_component(owner, 'local')}__{safe_component(repo)}"


def validate_archive_id(archive_id: str) -> None:
    """Reject archive ids that could escape the archives directory."""

    if (
        not archive_id
        or archive_id in {".", ".."}
        or "/" in archive_id
        or "\\" in archive_id
        or "\x00" in archive_id
    ):
        raise ArchiveError(f'invalid archive id "{archive_id}"')


def archive_dir(archives_dir: str | os.PathLike[str], archive_id: str) -> Path:
    """Return the archive directory for archive_id."""

    validate_archive_id(archive_id)
    return Path(archives_dir) / archive_id


def repo_name(archive_id: str) -> str:
    """Return the repository part of an archive id."""

    return archive_id.split("__", 1)[1] if "__" in archive_id else archive_id


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 with a Z suffix."""

    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _read_json(path: Path, *, what: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArchiveError(f'cannot read {what} "{path}": {exc}') from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArchiveError(f'{what} "{path}" is not valid JSON: {exc}') from exc


def write_json_atomic(path: str | os.PathLike[str], data: object) -> None:
    """Write JSON to path atomically (temp file + os.replace)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _read_mapping(path: Path, *, what: str) -> dict[str, object]:
    data = _read_json(path, what=what)
    if not isinstance(data, dict):
        raise ArchiveError(f'{what} "{path}" must contain a JSON object')
    return data


def read_meta(directory: str | os.PathLike[str]) -> dict[str, object]:
    """Read meta.json from an archive directory."""

    path = Path(directory) / "meta.json"
    if not path.is_file():
        raise ArchiveError(f'archive metadata not found: "{path}"')
    return _read_mapping(path, what="archive metadata")


def write_meta(directory: str | os.PathLike[str], meta: dict[str, object]) -> None:
    """Atomically write meta.json."""

    write_json_atomic(Path(directory) / "meta.json", meta)


def bundle_path(
    directory: str | os.PathLike[str],
    meta: dict[str, object] | None = None,
) -> Path | None:
    """Return the bundle path recorded in meta.json."""

    archive = Path(directory)
    data = read_meta(archive) if meta is None else meta
    raw = data.get("bundle", "code.bundle")
    if raw is None:
        return None
    path = Path(str(raw))
    return path if path.is_absolute() else archive / path


def read_state(directory: str | os.PathLike[str]) -> dict[str, object] | None:
    """Read state.json; return None when no tracker state exists."""

    path = Path(directory) / "state.json"
    if not path.is_file():
        return None
    return _read_mapping(path, what="tracker state")


def write_state(directory: str | os.PathLike[str], state: dict[str, object]) -> None:
    """Atomically write state.json for a tracked archive."""

    write_json_atomic(Path(directory) / "state.json", state)


def append_history(directory: str | os.PathLike[str], record: dict[str, object]) -> None:
    """Append one JSON line to history.jsonl."""

    path = Path(directory) / HISTORY_FILENAME
    entry = {"at": now_iso(), **record}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_history(
    directory: str | os.PathLike[str],
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Read history.jsonl; unparsable lines are skipped, not fatal."""

    path = Path(directory) / HISTORY_FILENAME
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    if limit is not None:
        return entries[-limit:] if limit > 0 else []
    return entries
