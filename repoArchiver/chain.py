"""Base bundle plus incremental delta bundles."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import layout, progress
from .bundle import create_bundle, git, list_heads
from .errors import ArchiveError, GitError, warn
from .i18n import tr

BASE_NAME = "code.bundle"
INCREMENT_PREFIX = "code-"
INCREMENT_SUFFIX = ".bundle"

_FETCH_REFSPECS = (
    "+refs/heads/*:refs/heads/*",
    "+refs/tags/*:refs/tags/*",
    "+refs/repoArchiver/*:refs/repoArchiver/*",
)


@dataclass(frozen=True)
class BundleEntry:
    """One file of the chain."""

    path: Path
    kind: str = "incremental"

    @property
    def name(self) -> str:
        return self.path.name


def chain_entries(
    archive_dir: str | os.PathLike[str],
    meta: dict[str, Any],
) -> list[BundleEntry]:
    """Return the bundle chain of an archive, base first."""

    archive = Path(archive_dir)
    raw = meta.get("bundle_chain")
    entries: list[BundleEntry] = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("path")
            if not isinstance(name, str) or not name:
                continue
            path = Path(name)
            if not path.is_absolute():
                path = archive / path
            kind = item.get("kind")
            entries.append(
                BundleEntry(path, kind if isinstance(kind, str) and kind else "incremental")
            )
        if entries:
            return entries
    single = layout.bundle_path(archive, meta)
    return [BundleEntry(single, "base")] if single is not None else []


def chain_tips(entries: list[BundleEntry]) -> list[str]:
    """Every commit a chain's refs point at."""

    tips: set[str] = set()
    for entry in entries:
        if entry.path.is_file():
            tips.update(commit for commit, _ref in list_heads(entry.path))
    return sorted(tips)


def chain_heads(entries: list[BundleEntry]) -> list[tuple[str, str]]:
    """Merged (commit, ref) view of a chain; later bundles win."""

    merged: dict[str, str] = {}
    for entry in entries:
        if not entry.path.is_file():
            continue
        for commit, ref in list_heads(entry.path):
            merged[ref] = commit
    return sorted(((commit, ref) for ref, commit in merged.items()), key=lambda pair: pair[1])


def increment_name(archive_dir: str | os.PathLike[str]) -> str:
    """A fresh, collision-free code-<timestamp>.bundle name."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = Path(archive_dir)
    name = f"{INCREMENT_PREFIX}{stamp}{INCREMENT_SUFFIX}"
    counter = 2
    while (archive / name).exists():
        name = f"{INCREMENT_PREFIX}{stamp}-{counter}{INCREMENT_SUFFIX}"
        counter += 1
    return name


def _has_commit(repo: str | os.PathLike[str], commit: str) -> bool:
    proc = git(
        ["cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, check=False
    )
    return proc.returncode == 0


def create_incremental(
    repo: str | os.PathLike[str],
    archive_dir: str | os.PathLike[str],
    entries: list[BundleEntry],
    refs: list[str],
) -> Path | None:
    """Write a delta bundle against the chain's current tips."""

    archive = Path(archive_dir)
    tips = chain_tips(entries)
    if not tips:
        return None
    if not all(_has_commit(repo, tip) for tip in tips):
        warn(
            tr(
                "the source no longer contains every commit of the existing bundle "
                "(force-push or deleted branch); rebuilding the full bundle"
            )
        )
        return None
    if not refs:
        return None

    name = increment_name(archive)
    temp = archive / f".{name}.{os.getpid()}.tmp"
    try:
        proc = git(
            ["bundle", "create", str(temp), *refs, "--not", *tips],
            cwd=repo,
            check=False,
            stream=True,
        )
        if proc.returncode != 0 or not temp.is_file() or temp.stat().st_size == 0:
            detail = f"{proc.stderr or proc.stdout}".strip().lower()
            temp.unlink(missing_ok=True)
            if not detail or "empty bundle" in detail:
                return None
            raise GitError(f"git bundle failed: {proc.stderr.strip()}")
        os.replace(temp, archive / name)
    finally:
        temp.unlink(missing_ok=True)
    return archive / name


def append_entry(meta: dict[str, Any], name: str, kind: str = "incremental") -> list[dict[str, Any]]:
    """Return the bundle_chain list with one more entry appended."""

    raw = meta.get("bundle_chain")
    chain = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if not chain:
        chain = [{"path": BASE_NAME, "kind": "base", "created_at": meta.get("archived_at")}]
    chain.append({"path": name, "kind": kind, "created_at": layout.now_iso()})
    return chain


def reset_chain(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """A single-entry chain pointing at the freshly written base bundle."""

    return [{"path": BASE_NAME, "kind": "base", "created_at": meta.get("archived_at")}]


def materialize(
    entries: list[BundleEntry],
    dest: str | os.PathLike[str],
    *,
    force: bool = False,
) -> None:
    """Recreate the archived refs (and history) in dest."""

    if not entries:
        raise ArchiveError("the archive stores no bundles")
    target = Path(dest)
    base = entries[0]
    if not base.path.is_file():
        raise ArchiveError(f'bundle not found: "{base.path}"')

    if target.exists():
        if force:
            shutil.rmtree(target)
        elif any(target.iterdir()):
            raise ArchiveError(f'destination "{target}" is not empty')
        else:
            target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)

    with progress.long_stage(tr("replaying {count} bundle(s)", count=len(entries))):
        git(["clone", "--quiet", "--", str(base.path.resolve()), str(target)], stream=True)
        git(["checkout", "--quiet", "--detach"], cwd=target, check=False)
        for entry in entries[1:]:
            if not entry.path.is_file():
                raise ArchiveError(f'bundle not found: "{entry.path}"')
            git(
                [
                    "fetch",
                    "--quiet",
                    "--force",
                    "--no-tags",
                    str(entry.path.resolve()),
                    *_FETCH_REFSPECS,
                ],
                cwd=target,
                stream=True,
            )


def verify_chain(entries: list[BundleEntry]) -> list[str]:
    """Replay the chain in a scratch repository; return the problems found."""

    problems: list[str] = []
    missing = [entry.name for entry in entries if not entry.path.is_file()]
    if missing:
        return [f"bundle file(s) missing: {', '.join(missing)}"]

    scratch = Path(entries[0].path).parent / f".verify-{uuid.uuid4().hex[:8]}"
    try:
        materialize(entries, scratch)
    except (ArchiveError, GitError) as exc:
        problems.append(f"bundle chain cannot be restored: {exc}")
        return problems
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return problems


def remove_incrementals(archive_dir: str | os.PathLike[str], keep_base: bool = True) -> list[Path]:
    """Delete code-*.bundle files, keeping the base by default."""

    archive = Path(archive_dir)
    removed: list[Path] = []
    for path in sorted(archive.glob(f"{INCREMENT_PREFIX}*{INCREMENT_SUFFIX}")):
        name = path.name
        if keep_base and name == BASE_NAME:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:  # pragma: no cover - unusual permissions
            warn(tr('cannot remove "{path}": {detail}', path=path, detail=exc))
    return removed


def prune(archive_dir: str | os.PathLike[str], keep: set[str]) -> list[Path]:
    """Remove bundle files that no longer appear in the chain."""

    archive = Path(archive_dir)
    removed: list[Path] = []
    for path in sorted(archive.glob(f"{INCREMENT_PREFIX}*{INCREMENT_SUFFIX}")):
        if path.name in keep:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:  # pragma: no cover
            warn(tr('cannot remove "{path}": {detail}', path=path, detail=exc))
    return removed


def compact(
    archive_dir: str | os.PathLike[str],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Fold the whole chain into one self-contained code.bundle."""

    archive = Path(archive_dir)
    entries = chain_entries(archive, meta)
    if not entries:
        raise ArchiveError("the archive stores no bundles")
    if len(entries) == 1 and entries[0].path.name == BASE_NAME:
        return meta

    scratch = archive / f".compact-{uuid.uuid4().hex[:8]}"
    try:
        materialize(entries, scratch)
        names = [
            ref
            for _commit, ref in chain_heads(entries)
            if not ref.startswith("refs/remotes/")
        ]
        create_bundle(scratch, archive / BASE_NAME, names)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    remove_incrementals(archive, keep_base=True)
    updated = dict(meta)
    updated["bundle"] = BASE_NAME
    updated["bundle_chain"] = reset_chain(updated)
    return updated


__all__ = [
    "BASE_NAME",
    "BundleEntry",
    "append_entry",
    "chain_entries",
    "chain_heads",
    "chain_tips",
    "compact",
    "create_incremental",
    "increment_name",
    "materialize",
    "prune",
    "remove_incrementals",
    "reset_chain",
    "verify_chain",
]
