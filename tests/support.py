"""Test helpers: temporary directories and small git commands."""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

# Plain mkdir instead of tempfile: its 0700 mode breaks under restricted Windows tokens.
_TEMP_BASE = Path(os.environ.get("REPOARCHIVER_TEST_TMP", Path.cwd() / ".test-tmp"))


def make_temp_dir(prefix: str = "case-") -> Path:
    """Create and return a fresh writable directory."""

    _TEMP_BASE.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = _TEMP_BASE / f"{prefix}{secrets.token_hex(6)}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("could not allocate a temporary directory")


def run_git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command in cwd and return the completed process."""

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
        check=check,
    )


def commit(cwd: Path, message: str, when: str | None = None) -> None:
    """Create a commit with deterministic identity and optional timestamp."""

    env = None
    if when:
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    run_git(
        cwd,
        "-c",
        "user.name=repoArchiver tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "--quiet",
        "-m",
        message,
        env=env,
    )
