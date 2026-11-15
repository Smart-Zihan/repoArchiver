"""Thin wrappers around git bundle and the git executable."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import deque
from pathlib import Path

from . import progress
from .errors import ArchiveError, GitError
from .i18n import tr

GIT = "git"
_TAIL_LINES = 40


def _environment(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if extra_env:
        env.update(extra_env)
    return env


def _progress_args(args: list[str]) -> list[str]:
    """Add --progress where git only shows it on a terminal otherwise."""

    if "--progress" in args or "--no-progress" in args:
        return args
    head = args[0] if args else ""
    if head in {"clone", "fetch"}:
        return [head, "--progress", *args[1:]]
    if head == "bundle" and len(args) > 1 and args[1] == "create":
        return [head, args[1], "--progress", *args[2:]]
    return args


def _split_line(buffer: bytes) -> tuple[bytes, bytes] | None:
    """Split buffer at the first CR or LF; return None if incomplete."""

    positions = [index for index in (buffer.find(b"\r"), buffer.find(b"\n")) if index >= 0]
    if not positions:
        return None
    cut = min(positions)
    return buffer[:cut], buffer[cut + 1 :]


def _run_streaming(
    args: list[str],
    cwd: str | os.PathLike[str] | None,
    reporter: progress.Reporter,
    *,
    check: bool,
    extra_env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """Run git, forwarding its progress stream live to reporter."""

    command = [GIT, *_progress_args(args)]
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_environment(extra_env),
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found; install git and retry") from exc
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc

    collected: list[str] = []
    tail: deque[str] = deque(maxlen=_TAIL_LINES)
    buffer = b""
    stream = proc.stdout
    try:
        while stream is not None:
            chunk = stream.read1(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                split = _split_line(buffer)
                if split is None:
                    break
                raw, buffer = split
                line = raw.decode("utf-8", "replace")
                if line.strip():
                    collected.append(line)
                    tail.append(line)
                    reporter.git(line)
        if buffer:
            line = buffer.decode("utf-8", "replace")
            if line.strip():
                collected.append(line)
                tail.append(line)
                reporter.git(line)
    finally:
        if stream is not None:
            stream.close()
        returncode = proc.wait()

    if check and returncode != 0:
        detail = "\n".join(tail).strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise GitError(f"git {args[0]} failed (exit {returncode}): {detail}")
    return subprocess.CompletedProcess(command, returncode, "\n".join(collected), "")


def git(
    args: list[str],
    cwd: str | os.PathLike[str] | None = None,
    *,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
    stdin: str | None = None,
    stream: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run git and return the completed process.

    With stream the merged output lands in stdout and stderr stays empty.
    """

    reporter = progress.progress() if stream else None
    if reporter is not None and reporter.enabled and stdin is None:
        return _run_streaming(args, cwd, reporter, check=check, extra_env=extra_env)

    command = [GIT, *args]
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_environment(extra_env),
            input=stdin,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found; install git and retry") from exc
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise GitError(f"git {args[0]} failed (exit {proc.returncode}): {detail}")
    return proc


def create_bundle(
    src: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    refs: list[str] | None = None,
) -> None:
    """Create a bundle of the local repository at src."""

    source = Path(src)
    target = Path(out_path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    git(["rev-parse", "--git-dir"], cwd=source)

    if refs is None:
        existing = git(["for-each-ref", "--format=%(objectname) %(refname)"], cwd=source)
        if not existing.stdout.strip():
            raise ArchiveError(f'repository "{source}" has no refs; nothing to archive')
        revision_args = ["--all"]
    else:
        if not refs:
            raise ArchiveError("no refs selected; nothing to archive")
        revision_args = list(refs)

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with progress.long_stage(tr("writing {name}", name=target.name)):
            git(
                ["bundle", "create", str(temp_path), *revision_args],
                cwd=source,
                stream=True,
            )
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise ArchiveError("git bundle produced no output")
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)



def list_heads(bundle_path: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Return (commit_hash, full_ref_name) pairs stored in a bundle."""

    path = Path(bundle_path)
    if not path.is_file():
        raise ArchiveError(f'bundle not found: "{path}"')

    proc = git(["bundle", "list-heads", str(path)])
    refs: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        commit, ref = parts[0].strip(), parts[1].strip()
        if not ref.startswith("refs/"):
            continue
        refs.append((commit, ref))
    refs.sort(key=lambda item: item[1])
    return refs


def rev_parse_commit(repo: str | os.PathLike[str], revision: str) -> str | None:
    """Resolve revision to a commit hash, or return None."""

    proc = git(
        ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        cwd=repo,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _link_or_copy(source: str, target: str) -> str:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def _git_dir_of(repo: str | os.PathLike[str]) -> Path | None:
    """Return the git directory of a local repository, if it can be found."""

    path = Path(repo)
    dot_git = path / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        if text.lower().startswith("gitdir:"):
            target = Path(text[7:].strip())
            if not target.is_absolute():
                target = (path / target).resolve()
            return target if target.is_dir() else None
    if (path / "HEAD").is_file() and (path / "objects").is_dir():
        return path
    return None


def mirror_source(
    repo: str | os.PathLike[str],
    dest: str | os.PathLike[str],
    *,
    copy_objects: bool = False,
) -> None:
    """Copy a local repository into a private working mirror at dest."""

    target = Path(dest)
    if not copy_objects:
        with progress.long_stage(tr("cloning {source} into a work mirror", source=str(repo))):
            clone = git(
                ["clone", "--mirror", "--quiet", "--", str(repo), str(target)],
                check=False,
                stream=True,
            )
        if clone.returncode == 0:
            return

    git_dir = _git_dir_of(repo)
    if git_dir is None:
        detail = ""
        if not copy_objects:
            detail = (clone.stderr or clone.stdout or "").strip()
        raise GitError(f"cannot create a mirror of \"{repo}\": {detail or 'not a git repository'}")

    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with progress.long_stage(tr("copying the git directory of {source}", source=str(repo))):
        shutil.copytree(git_dir, target, dirs_exist_ok=True, copy_function=_link_or_copy)

