"""Rewrite history so a cutoff keeps real commits instead of a snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import tags
from .bundle import create_bundle, git
from .cutoff import Cutoff
from .errors import ArchiveError, warn

_FIELDS = ("%H", "%T", "%P", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B")
_FORMAT = "%x00".join(_FIELDS) + "%x01"
_CHUNK = 400


@dataclass
class RewriteResult:
    """What a rewrite produced."""

    refs: list[tuple[str, str]] = field(default_factory=list)  # (new commit, ref)
    commits: int = 0
    dropped_refs: list[str] = field(default_factory=list)
    old_tips: dict[str, str] = field(default_factory=dict)  # ref -> original commit

    def describe(self) -> str:
        return f"{self.commits} commit(s) kept in {len(self.refs)} ref(s)"

    def ref_map(self) -> dict[str, dict[str, str]]:
        """Return the ref mapping recorded in meta.json."""

        return {
            ref: {"from": self.old_tips.get(ref, ""), "to": commit}
            for commit, ref in self.refs
        }


@dataclass(frozen=True)
class _Commit:
    sha: str
    tree: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str
    message: str


def _read_commits(repo: str, shas: list[str]) -> dict[str, _Commit]:
    """Read commits through git log, NUL separated."""

    found: dict[str, _Commit] = {}
    for start in range(0, len(shas), _CHUNK):
        chunk = shas[start : start + _CHUNK]
        proc = git(
            ["log", "--no-walk=unsorted", f"--format={_FORMAT}", *chunk],
            cwd=repo,
            check=False,
        )
        if proc.returncode != 0:
            raise ArchiveError(f"cannot read commits: {(proc.stderr or '').strip()}")
        for record in proc.stdout.split("\x01"):
            record = record.strip("\n")
            if not record:
                continue
            parts = record.split("\x00")
            if len(parts) < len(_FIELDS):
                continue
            (sha, tree, parents, an, ae, a_date, cn, ce, c_date, message) = parts[:10]
            found[sha] = _Commit(
                sha=sha,
                tree=tree,
                parents=tuple(p for p in parents.split() if p),
                author_name=an,
                author_email=ae,
                author_date=a_date,
                committer_name=cn,
                committer_email=ce,
                committer_date=c_date,
                message=message,
            )
    return found


def _create_commit(repo: str, commit: _Commit, parents: list[str]) -> str:
    env = {
        "GIT_AUTHOR_NAME": commit.author_name or "repoArchiver",
        "GIT_AUTHOR_EMAIL": commit.author_email or "repoArchiver@localhost",
        "GIT_COMMITTER_NAME": commit.committer_name or "repoArchiver",
        "GIT_COMMITTER_EMAIL": commit.committer_email or "repoArchiver@localhost",
    }
    for key, value in (
        ("GIT_AUTHOR_DATE", commit.author_date),
        ("GIT_COMMITTER_DATE", commit.committer_date),
    ):
        if value:
            env[key] = value

    args = ["commit-tree", commit.tree]
    for parent in parents:
        args.extend(["-p", parent])
    message = commit.message or f"repoArchiver: rewritten commit {commit.sha}\n"
    if not message.endswith("\n"):
        message += "\n"
    proc = git(args + ["-F", "-"], cwd=repo, extra_env=env, stdin=message)
    return proc.stdout.strip()


def rewrite_repo(
    repo: str,
    refs: list[tuple[str, str]],
    cutoff: Cutoff,
) -> RewriteResult:
    """Rewrite every commit at/after the cutoff; update the refs in repo."""

    if not refs:
        raise ArchiveError(
            f"no branches or tags at/after the cutoff ({cutoff.describe()})"
        )

    tips = [ref for _commit, ref in refs]
    proc = git(
        ["rev-list", "--topo-order", "--reverse", "--timestamp", *tips],
        cwd=repo,
    )
    ordered: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        stamp, _space, sha = line.strip().partition(" ")
        if sha:
            try:
                ordered.append((int(stamp), sha))
            except ValueError:
                continue

    boundary = cutoff.instant.timestamp()
    kept_shas = [sha for stamp, sha in ordered if stamp >= boundary]
    if not kept_shas:
        raise ArchiveError(
            f"no commit at/after the cutoff ({cutoff.describe()}); nothing to archive"
        )

    commits = _read_commits(repo, kept_shas)
    mapping: dict[str, str] = {}
    result = RewriteResult()
    for sha in kept_shas:
        commit = commits.get(sha)
        if commit is None:
            continue
        parents = [mapping[parent] for parent in commit.parents if parent in mapping]
        rewritten = _create_commit(repo, commit, parents)
        mapping[sha] = rewritten
        result.commits += 1

    for commit_hash, ref in refs:
        new_commit = mapping.get(commit_hash)
        if new_commit is None:
            result.dropped_refs.append(ref)
            continue
        if ref.startswith("refs/tags/"):
            name = ref[len("refs/tags/") :]
            info = tags.read_tag(repo, ref)
            if info is not None:
                tags.recreate_annotated_tag(repo, name, new_commit, info)
            else:
                git(["update-ref", ref, new_commit], cwd=repo)
        else:
            git(["update-ref", ref, new_commit], cwd=repo)
        result.refs.append((new_commit, ref))
        result.old_tips[ref] = commit_hash

    if result.dropped_refs:
        warn(
            "ref(s) entirely before the cutoff were dropped: "
            + ", ".join(sorted(result.dropped_refs))
        )
    result.refs.sort(key=lambda pair: pair[1])
    return result


def build_rewritten_bundle(
    repo: str,
    out_path: str,
    refs: list[tuple[str, str]],
    cutoff: Cutoff,
) -> RewriteResult:
    """Rewrite the refs of repo and write a bundle containing only the new ones."""

    result = rewrite_repo(repo, refs, cutoff)
    create_bundle(repo, out_path, [ref for _commit, ref in result.refs])
    return result


def cutoff_from_meta(meta: dict) -> Cutoff | None:
    """Rebuild a Cutoff from the cutoff entry in meta.json."""

    raw = meta.get("cutoff")
    if not isinstance(raw, dict):
        return None
    instant = raw.get("instant")
    if not isinstance(instant, str):
        return None
    from .cutoff import parse_instant

    try:
        when: datetime = parse_instant(instant)
    except ArchiveError:
        return None
    return Cutoff(
        when,
        label=str(raw.get("since") or raw.get("since_tag") or instant),
        since=raw.get("since") if isinstance(raw.get("since"), str) else None,
        since_tag=raw.get("since_tag") if isinstance(raw.get("since_tag"), str) else None,
    )


__all__ = ["RewriteResult", "build_rewritten_bundle", "cutoff_from_meta", "rewrite_repo"]
