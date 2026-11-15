"""Read-only consistency checks for an archive."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import chain, http, layout
from .errors import RepoArchiverError
from .i18n import tr
from .releases import read_index

ERROR = "error"
WARNING = "warning"
INFO = "info"

_SCOPES = ("all", "code", "releases")


@dataclass(frozen=True)
class Problem:
    """One finding."""

    level: str
    code: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.level == ERROR

    def to_json(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass
class Report:
    """All findings for one archive."""

    archive_id: str
    path: Path
    problems: list[Problem]

    @property
    def errors(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.level == ERROR]

    @property
    def warnings(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        return {
            "archive": self.archive_id,
            "path": str(self.path),
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "problems": [problem.to_json() for problem in self.problems],
        }


def _asset_path(asset: dict[str, Any], archive: Path) -> Path | None:
    local = asset.get("local_path")
    if not isinstance(local, str) or not local:
        return None
    path = Path(local)
    if not path.is_absolute():
        path = archive / path
    return path


def _check_meta(archive: Path, problems: list[Problem]) -> dict[str, Any] | None:
    try:
        meta = layout.read_meta(archive)
    except RepoArchiverError as exc:
        problems.append(Problem(ERROR, "meta-unreadable", str(exc)))
        return None

    source = meta.get("source")
    if not isinstance(source, str) or not source.strip():
        problems.append(Problem(ERROR, "meta-source", "meta.json has no source"))
    if not isinstance(meta.get("archived_at"), str):
        problems.append(Problem(WARNING, "meta-archived-at", "meta.json has no archived_at"))
    scope = meta.get("scope", "all")
    if scope not in _SCOPES:
        problems.append(
            Problem(ERROR, "meta-scope", f'meta.json has an unknown scope "{scope}"')
        )
    return meta


def _check_bundles(
    archive: Path,
    meta: dict[str, Any],
    problems: list[Problem],
) -> None:
    entries = chain.chain_entries(archive, meta)
    if not entries:
        if meta.get("scope") in ("all", "code"):
            problems.append(
                Problem(ERROR, "bundle-missing", "meta.json records no bundle for a code archive")
            )
        return

    missing = [entry.name for entry in entries if not entry.path.is_file()]
    if missing:
        problems.append(
            Problem(ERROR, "bundle-missing", f"missing bundle file(s): {', '.join(missing)}")
        )
        return

    for problem in chain.verify_chain(entries):
        problems.append(Problem(ERROR, "bundle-chain", problem))

    try:
        heads = chain.chain_heads(entries)
    except RepoArchiverError as exc:
        problems.append(Problem(ERROR, "bundle-heads", str(exc)))
        return

    expected = meta.get("refs_count")
    if isinstance(expected, int) and expected != len(heads):
        problems.append(
            Problem(
                WARNING,
                "refs-count",
                f"meta.json says refs_count={expected}, the bundle holds {len(heads)}",
            )
        )
    if not heads:
        problems.append(Problem(ERROR, "bundle-empty", "the bundle contains no refs"))

    internal = [ref for _commit, ref in heads if ref.startswith("refs/repoArchiver/")]
    if internal:
        problems.append(
            Problem(
                INFO,
                "internal-refs",
                f"{len(internal)} internal ref(s) pin orphan commits",
            )
        )


def _check_releases(
    archive: Path,
    meta: dict[str, Any],
    problems: list[Problem],
) -> None:
    index_path = archive / "releases" / "index.json"
    try:
        releases = read_index(index_path)
    except RepoArchiverError as exc:
        problems.append(Problem(ERROR, "index-unreadable", str(exc)))
        return

    expected = meta.get("release_count")
    if isinstance(expected, int) and expected != len(releases):
        problems.append(
            Problem(
                WARNING,
                "release-count",
                f"meta.json says release_count={expected}, index.json holds {len(releases)}",
            )
        )
    if meta.get("scope") in ("all", "releases") and not index_path.is_file():
        problems.append(
            Problem(ERROR, "index-missing", "releases/index.json is missing")
        )

    tags: set[str] = set()
    saved = total = 0
    for release in releases:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag:
            problems.append(
                Problem(WARNING, "release-tag", "a release entry has no tag_name")
            )
            continue
        if tag in tags:
            problems.append(
                Problem(WARNING, "release-duplicate", f'duplicate release tag "{tag}"')
            )
        tags.add(tag)
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            total += 1
            path = _asset_path(asset, archive)
            if path is None:
                continue
            name = f'{tag}/{asset.get("name")}'
            if not path.is_file():
                problems.append(
                    Problem(
                        ERROR,
                        "asset-missing",
                        f'index records asset "{name}" at "{path}", which does not exist',
                    )
                )
                continue
            saved += 1
            size = asset.get("size")
            if isinstance(size, int) and path.stat().st_size != size:
                problems.append(
                    Problem(
                        ERROR,
                        "asset-size",
                        f'asset "{name}" is {path.stat().st_size} bytes, '
                        f"the index says {size}",
                    )
                )

    if meta.get("with_assets") and total and not saved:
        problems.append(
            Problem(
                WARNING,
                "assets-none",
                "the archive is marked as with-assets but no asset file is present",
            )
        )


def _check_state(
    archive: Path,
    meta: dict[str, Any],
    problems: list[Problem],
) -> None:
    state_path = archive / "state.json"
    if not state_path.is_file():
        return
    try:
        state = layout.read_state(archive) or {}
    except RepoArchiverError as exc:
        problems.append(Problem(ERROR, "state-unreadable", str(exc)))
        return
    if not state:
        problems.append(Problem(WARNING, "state-empty", "state.json is empty"))
        return
    if not isinstance(state.get("tracker"), str):
        problems.append(
            Problem(WARNING, "state-tracker", "state.json has no tracker label")
        )
    source = state.get("source")
    if isinstance(source, str) and meta.get("source") and source != meta.get("source"):
        problems.append(
            Problem(
                WARNING,
                "state-source",
                f'state.json tracks "{source}", meta.json says "{meta.get("source")}"',
            )
        )
    scope = state.get("scope")
    if isinstance(scope, str) and scope not in _SCOPES:
        problems.append(
            Problem(WARNING, "state-scope", f'state.json has an unknown scope "{scope}"')
        )


def _check_history(archive: Path, problems: list[Problem]) -> None:
    path = archive / layout.HISTORY_FILENAME
    if not path.is_file():
        return
    bad = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                bad += 1
    except OSError as exc:
        problems.append(Problem(WARNING, "history-unreadable", str(exc)))
        return
    if bad:
        problems.append(
            Problem(WARNING, "history-lines", f"history.jsonl has {bad} unparsable line(s)")
        )


def _check_partials(archive: Path, problems: list[Problem]) -> None:
    partials = http.leftovers(archive)
    if partials:
        names = ", ".join(str(path.relative_to(archive)) for path in partials[:5])
        more = "" if len(partials) <= 5 else f" (+{len(partials) - 5} more)"
        problems.append(
            Problem(
                WARNING,
                "partial-downloads",
                f"unfinished download(s) left behind: {names}{more} "
                "(a later run resumes them)",
            )
        )


def verify_archive(directory: str | Path) -> Report:
    """Check one archive directory and return its findings."""

    archive = Path(directory).expanduser()
    path = archive.resolve() if archive.exists() else archive
    problems: list[Problem] = []
    if not archive.is_dir():
        problems.append(Problem(ERROR, "archive-missing", f'archive not found: "{archive}"'))
        return Report(archive_id=archive.name, path=path, problems=problems)

    meta = _check_meta(archive, problems)
    if meta is not None:
        _check_bundles(archive, meta, problems)
        _check_releases(archive, meta, problems)
        _check_state(archive, meta, problems)
    _check_history(archive, problems)
    _check_partials(archive, problems)
    return Report(archive_id=archive.name, path=path, problems=problems)


def verify_all(archives_dir: str | Path, ids: Iterable[str | Path] | None = None) -> list[Report]:
    """Verify the named archives, or every archive under archives_dir."""

    root = Path(archives_dir)
    if ids:
        targets: list[Path] = []
        for item in ids:
            path = Path(item)
            targets.append(path if path.is_absolute() else root / path)
        return [verify_archive(target) for target in targets]
    if not root.is_dir():
        return []
    return [
        verify_archive(candidate)
        for candidate in sorted(root.iterdir())
        if candidate.is_dir() and (candidate / "meta.json").is_file()
    ]


def summarize(reports: list[Report]) -> str:
    """One-line tally used by the CLI footer."""

    errors = sum(len(report.errors) for report in reports)
    warnings = sum(len(report.warnings) for report in reports)
    if len(reports) == 1:
        return tr(
            "{count} archive checked, {errors} error(s), {warnings} warning(s)",
            count=1,
            errors=errors,
            warnings=warnings,
        )
    return tr(
        "{count} archives checked, {errors} error(s), {warnings} warning(s)",
        count=len(reports),
        errors=errors,
        warnings=warnings,
    )


__all__ = [
    "ERROR",
    "INFO",
    "Problem",
    "Report",
    "WARNING",
    "summarize",
    "verify_all",
    "verify_archive",
]
