"""Ref selection globs and orphan-commit preservation."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .bundle import git
from .errors import ArchiveError
from .i18n import tr

INTERNAL_PREFIX = "refs/repoArchiver/"
ORPHAN_PREFIX = f"{INTERNAL_PREFIX}orphans/"


def _patterns(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    try:
        items = list(values)
    except TypeError:
        return ()
    return tuple(item.strip() for item in items if isinstance(item, str) and item.strip())


@dataclass(frozen=True)
class RefRules:
    """Include/exclude rules over full ref names (git glob semantics)."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    keep_orphans: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "include", _patterns(self.include))
        object.__setattr__(self, "exclude", _patterns(self.exclude))

    @staticmethod
    def _expand(pattern: str) -> list[str]:
        """Expand a short pattern to branch and tag ref names."""

        if pattern.startswith("refs/"):
            return [pattern]
        return [f"refs/heads/{pattern}", f"refs/tags/{pattern}"]

    @classmethod
    def _match(cls, patterns: Iterable[str], ref: str) -> bool:
        return any(
            fnmatch.fnmatchcase(ref, candidate)
            for pattern in patterns
            for candidate in cls._expand(pattern)
        )

    def allows(self, ref: str) -> bool:
        """Whether ref belongs in the archive."""

        if ref.startswith(INTERNAL_PREFIX):
            return False
        if self.include and not self._match(self.include, ref):
            return False
        return not (self.exclude and self._match(self.exclude, ref))

    def apply(self, refs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        """Filter (commit, ref) pairs; internal refs are dropped."""

        return [pair for pair in refs if self.allows(pair[1])]

    def is_default(self) -> bool:
        return not self.include and not self.exclude and not self.keep_orphans

    def to_json(self) -> dict[str, Any]:
        return {
            "include": list(self.include),
            "exclude": list(self.exclude),
            "keep_orphans": self.keep_orphans,
        }

    @classmethod
    def from_json(cls, data: Any) -> RefRules:
        if not isinstance(data, dict):
            return cls()
        keep = data.get("keep_orphans", False)
        return cls(
            include=_patterns(data.get("include")),
            exclude=_patterns(data.get("exclude")),
            keep_orphans=keep if isinstance(keep, bool) else False,
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.include:
            parts.append("include=" + ",".join(self.include))
        if self.exclude:
            parts.append("exclude=" + ",".join(self.exclude))
        if self.keep_orphans:
            parts.append("orphans")
        return "; ".join(parts) if parts else "all refs"


def repo_refs(repo: str) -> list[tuple[str, str]]:
    """Return (objectname, refname) for every branch and tag of repo."""

    proc = git(
        ["for-each-ref", "--format=%(objectname) %(refname)", "refs/heads/", "refs/tags/"],
        cwd=repo,
    )
    pairs: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pairs.append((parts[0].strip(), parts[1].strip()))
    pairs.sort(key=lambda item: item[1])
    return pairs


def visible_refs(refs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop repoArchiver's internal refs."""

    return [pair for pair in refs if not pair[1].startswith(INTERNAL_PREFIX)]


def unreachable_commits(repo: str) -> list[str]:
    """Commits no ref points at (dangling/unreachable objects)."""

    proc = git(
        ["fsck", "--unreachable", "--dangling", "--no-reflogs", "--no-progress"],
        cwd=repo,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for line in f"{proc.stdout}\n{proc.stderr}".splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] in {"unreachable", "dangling"} and parts[1] == "commit":
            sha = parts[2]
            if sha not in seen:
                seen.add(sha)
                found.append(sha)
    return found


def _parents_of(repo: str, commits: list[str]) -> dict[str, list[str]]:
    if not commits:
        return {}
    proc = git(
        ["rev-list", "--no-walk", "--parents", "--stdin"],
        cwd=repo,
        stdin="\n".join(commits) + "\n",
    )
    result: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if parts:
            result[parts[0]] = parts[1:]
    return result


def orphan_tips(repo: str) -> list[str]:
    """Unreachable commits that are not an ancestor of another unreachable one."""

    commits = unreachable_commits(repo)
    if not commits:
        return []
    parents = _parents_of(repo, commits)
    known = set(commits)
    covered = {
        parent
        for values in parents.values()
        for parent in values
        if parent in known
    }
    return sorted(sha for sha in commits if sha not in covered)


def install_orphan_refs(repo: str) -> list[tuple[str, str]]:
    """Pin orphan tips under refs/repoArchiver/orphans/ and return them."""

    tips = orphan_tips(repo)
    result: list[tuple[str, str]] = []
    for sha in tips:
        ref = f"{ORPHAN_PREFIX}{sha}"
        git(["update-ref", ref, sha], cwd=repo)
        result.append((sha, ref))
    return result


def remove_internal_refs(repo: str) -> None:
    """Delete stale refs under refs/repoArchiver/."""

    proc = git(
        ["for-each-ref", "--format=%(refname)", INTERNAL_PREFIX],
        cwd=repo,
        check=False,
    )
    for ref in proc.stdout.splitlines():
        ref = ref.strip()
        if ref:
            git(["update-ref", "-d", ref], cwd=repo, check=False)


def select_for_bundle(
    repo: str,
    rules: RefRules,
    base: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Return the (commit, ref) pairs to bundle, applying rules."""

    candidates = repo_refs(repo) if base is None else list(base)
    selected = rules.apply(candidates)
    if not selected:
        raise ArchiveError(
            tr(
                "no refs left after applying {rules}; nothing to archive",
                rules=rules.describe(),
            )
        )
    if rules.keep_orphans:
        remove_internal_refs(repo)
        orphans = install_orphan_refs(repo)
        selected.extend(orphans)
    selected.sort(key=lambda item: item[1])
    return selected


def bundle_refs_args(refs: list[tuple[str, str]]) -> list[str]:
    """Full ref names, ready for git bundle create."""

    return [ref for _commit, ref in refs]


__all__ = [
    "INTERNAL_PREFIX",
    "ORPHAN_PREFIX",
    "RefRules",
    "bundle_refs_args",
    "install_orphan_refs",
    "orphan_tips",
    "remove_internal_refs",
    "repo_refs",
    "select_for_bundle",
    "unreachable_commits",
    "visible_refs",
]
