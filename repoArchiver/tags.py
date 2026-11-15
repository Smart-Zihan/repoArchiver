"""Reading and re-creating annotated tags."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .bundle import git

_IDENTITY = re.compile(r"^(?P<name>.*) <(?P<email>[^>]*)> (?P<stamp>[^ ]+) (?P<tz>[+-]\d{4})$")


@dataclass(frozen=True)
class Identity:
    name: str
    email: str
    when: str  # "<unix> <tz>", the format GIT_*_DATE accepts

    @classmethod
    def parse(cls, value: str, fallback_name: str = "repoArchiver") -> Identity:
        match = _IDENTITY.match(value.strip())
        if not match:
            return cls(fallback_name, f"{fallback_name}@localhost", "")
        return cls(
            match.group("name") or fallback_name,
            match.group("email") or f"{fallback_name}@localhost",
            f"{match.group('stamp')} {match.group('tz')}",
        )


@dataclass(frozen=True)
class TagInfo:
    """An annotated tag object."""

    name: str
    target: str
    tagger: Identity
    message: str


def object_type(repo: str, revision: str) -> str | None:
    proc = git(["cat-file", "-t", revision], cwd=repo, check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def read_tag(repo: str, ref: str) -> TagInfo | None:
    """Return the annotated tag object behind ref, or None."""

    if object_type(repo, ref) != "tag":
        return None
    proc = git(["cat-file", "-p", ref], cwd=repo, check=False)
    if proc.returncode != 0:
        return None
    return parse_tag_object(proc.stdout)


def parse_tag_object(text: str) -> TagInfo | None:
    """Parse git cat-file -p output of a tag object."""

    header, _sep, message = text.partition("\n\n")
    fields: dict[str, str] = {}
    for line in header.splitlines():
        key, _space, value = line.partition(" ")
        fields.setdefault(key, value)
    target = fields.get("object")
    if not target:
        return None
    tagger = Identity.parse(fields.get("tagger", ""), fallback_name="repoArchiver")
    return TagInfo(
        name=fields.get("tag", ""),
        target=target,
        tagger=tagger,
        message=message,
    )


def recreate_annotated_tag(
    repo: str,
    name: str,
    target_commit: str,
    info: TagInfo,
) -> None:
    """Re-create annotated tag name pointing at target_commit."""

    env = {
        "GIT_COMMITTER_NAME": info.tagger.name,
        "GIT_COMMITTER_EMAIL": info.tagger.email,
    }
    if info.tagger.when:
        env["GIT_COMMITTER_DATE"] = info.tagger.when
    message = info.message
    if not message.strip():
        message = f"repoArchiver: re-created tag {name}\n"
    git(
        ["tag", "-a", "-f", name, target_commit, "-F", "-"],
        cwd=repo,
        extra_env=env,
        stdin=message,
    )


__all__ = [
    "Identity",
    "TagInfo",
    "object_type",
    "parse_tag_object",
    "read_tag",
    "recreate_annotated_tag",
]
