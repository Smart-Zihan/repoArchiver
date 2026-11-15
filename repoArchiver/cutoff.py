"""Cutoff resolution for --since / --since-tag, and snapshot bundles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import tags
from .bundle import git, list_heads, rev_parse_commit
from .errors import ArchiveError, warn
from .i18n import tr
from .releases import find_release


@dataclass(frozen=True)
class Cutoff:
    """An inclusive lower bound for releases and (snapshot) refs."""

    instant: datetime
    label: str
    since: str | None = None
    since_tag: str | None = None

    def describe(self) -> str:
        source = f"--since {self.since}" if self.since else f"--since-tag {self.since_tag}"
        return f"{source} ({self.instant.isoformat()})"


def parse_instant(value: str) -> datetime:
    """Parse an ISO-8601 date/datetime; a missing offset means local time."""

    text = (value or "").strip()
    if not text:
        raise ArchiveError("empty date value")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ArchiveError(
            f'invalid date "{value}" (use ISO 8601, e.g. 2024-01-01 or 2024-01-01T12:00:00Z)'
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _parse_release_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_instant(value)
    except ArchiveError:
        return None


def _resolve_tag_name(repo: str | os.PathLike[str], tag: str) -> str:
    candidates = [tag]
    if tag.startswith("v") and len(tag) > 1:
        candidates.append(tag[1:])
    else:
        candidates.append(f"v{tag}")
    for candidate in candidates:
        if rev_parse_commit(repo, f"refs/tags/{candidate}") is not None:
            return candidate
    raise ArchiveError(f'tag "{tag}" not found; cannot use it as a cutoff')


def _resolve_release_tag(releases: list[dict[str, Any]], tag: str) -> str:
    """Resolve a cutoff tag against release metadata only."""

    candidates = [tag]
    if tag.startswith("v") and len(tag) > 1:
        candidates.append(tag[1:])
    else:
        candidates.append(f"v{tag}")
    for candidate in candidates:
        if find_release(releases, candidate) is not None:
            return candidate
    raise ArchiveError(f'tag "{tag}" not found in the release list; cannot use it as a cutoff')


def resolve_cutoff(
    repo: str | os.PathLike[str] | None,
    releases: list[dict[str, Any]],
    since: str | None = None,
    since_tag: str | None = None,
) -> Cutoff | None:
    """Turn --since / --since-tag into a concrete instant."""

    if since and since_tag:
        raise ArchiveError("use either --since or --since-tag, not both")
    if since:
        return Cutoff(parse_instant(since), label=since, since=since)
    if not since_tag:
        return None

    tag = (
        _resolve_tag_name(repo, since_tag)
        if repo is not None
        else _resolve_release_tag(releases, since_tag)
    )
    release = find_release(releases, tag)
    published = _parse_release_date((release or {}).get("published_at"))
    if published is not None:
        return Cutoff(published, label=since_tag, since_tag=tag)
    if repo is None:
        raise ArchiveError(
            f'tag "{tag}" has no publication date, and there is no repository '
            "to read its commit date from"
        )

    proc = git(["log", "-1", "--format=%cI", f"refs/tags/{tag}"], cwd=repo, check=False)
    stamp = _parse_release_date(proc.stdout.strip()) if proc.returncode == 0 else None
    if stamp is None:
        raise ArchiveError(f'cannot determine the date of tag "{tag}"')
    return Cutoff(stamp, label=since_tag, since_tag=tag)


def filter_releases(releases: list[dict[str, Any]], cutoff: Cutoff) -> list[dict[str, Any]]:
    """Keep releases published at/after cutoff; warn about skipped ones."""

    kept: list[dict[str, Any]] = []
    undated = 0
    for release in releases:
        published = _parse_release_date(release.get("published_at"))
        if published is None:
            undated += 1
            continue
        if published >= cutoff.instant:
            kept.append(release)
    if undated:
        warn(
            tr(
                "skipped {count} release(s) without a parseable publication date ({cutoff})",
                count=undated,
                cutoff=cutoff.describe(),
            )
        )
    return kept


def select_refs(
    repo: str | os.PathLike[str],
    cutoff: Cutoff | None = None,
) -> list[tuple[str, str]]:
    """Return (commit_hash, full_ref) for branches/tags at/after cutoff."""

    proc = git(
        ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/tags/"],
        cwd=repo,
    )
    selected: list[tuple[str, str]] = []
    skipped = 0
    for ref in proc.stdout.splitlines():
        ref = ref.strip()
        if not ref:
            continue
        info = git(
            ["log", "-1", "--format=%H%x00%cI", ref],
            cwd=repo,
            check=False,
        )
        if info.returncode != 0:
            skipped += 1
            continue
        commit, _, stamp = info.stdout.strip().partition("\x00")
        if not commit:
            skipped += 1
            continue
        if cutoff is not None:
            when = _parse_release_date(stamp)
            if when is None or when < cutoff.instant:
                continue
        selected.append((commit, ref))
    if skipped:
        warn(tr("skipped {count} ref(s) that do not point to a commit", count=skipped))
    selected.sort(key=lambda item: item[1])
    return selected


def _snapshot_commit(repo: Path, commit: str, ref: str) -> str:
    tree = git(["rev-parse", f"{commit}^{{tree}}"], cwd=repo).stdout.strip()
    info = git(
        ["show", "-s", "--format=%an%x00%ae%x00%aI%x00%s", commit],
        cwd=repo,
    ).stdout.rstrip("\n").split("\x00")
    name, email, when, subject = (info + ["", "", "", ""])[:4]
    message = (
        f"repoArchiver snapshot of {ref}\n\n"
        f"Original commit: {commit}\n\n"
        f"{subject}\n"
    )
    env = {
        "GIT_AUTHOR_NAME": name or "repoArchiver",
        "GIT_AUTHOR_EMAIL": email or "repoArchiver@localhost",
        "GIT_COMMITTER_NAME": "repoArchiver",
        "GIT_COMMITTER_EMAIL": "repoArchiver@localhost",
    }
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    proc = git(["commit-tree", tree, "-m", message], cwd=repo, extra_env=env)
    return proc.stdout.strip()


def _repoint(repo: Path, ref: str, commit: str) -> None:
    """Point ref at commit, keeping an annotated tag's message and tagger."""

    if ref.startswith("refs/tags/"):
        info = tags.read_tag(str(repo), ref)
        if info is not None:
            tags.recreate_annotated_tag(str(repo), ref[len("refs/tags/") :], commit, info)
            return
    git(["update-ref", ref, commit], cwd=repo)


def build_snapshot_bundle(
    repo: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    refs: list[tuple[str, str]],
) -> None:
    """Write a bundle whose refs point at single, history-less snapshots."""

    repository = Path(repo)
    target = Path(out_path)
    if not refs:
        raise ArchiveError("no refs to snapshot")

    for commit, ref in refs:
        snapshot = _snapshot_commit(repository, commit, ref)
        _repoint(repository, ref, snapshot)

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        git(
            ["bundle", "create", str(temp_path), *(ref for _commit, ref in refs)],
            cwd=repository,
        )
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise ArchiveError("git bundle produced no output")
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)

    heads = list_heads(target)
    if not heads:
        raise ArchiveError(f'snapshot bundle "{target}" contains no refs')
