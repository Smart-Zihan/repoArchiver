"""Release-source platforms and host detection."""

from __future__ import annotations

from typing import Any

from .. import layout
from ..errors import ConfigError
from .base import (
    KIND_ASSET,
    KIND_SOURCE,
    SOURCE_SUFFIXES,
    Platform,
    RepoRef,
    UnsupportedPlatform,
    make_asset,
    make_release,
    source_asset_name,
)
from .bitbucket import BitbucketPlatform
from .gitea import GiteaPlatform
from .github import GITHUB_HOSTS, GitHubPlatform, normalize_release
from .gitlab import GitLabPlatform

PLATFORMS = {
    "github": GitHubPlatform,
    "gitlab": GitLabPlatform,
    "gitea": GiteaPlatform,
    "bitbucket": BitbucketPlatform,
}

PLATFORM_NAMES = tuple(PLATFORMS)


def detect_platform(host: str) -> str | None:
    """Guess the forge behind host from its name."""

    host = (host or "").lower()
    if not host:
        return None
    if host in GITHUB_HOSTS:
        return "github"
    if host == "bitbucket.org":
        return "bitbucket"
    if host == "gitlab.com" or host.startswith("gitlab.") or ".gitlab." in host:
        return "gitlab"
    if (
        host in {"codeberg.org", "gitea.com", "try.gitea.io"}
        or "gitea" in host
        or "forgejo" in host
    ):
        return "gitea"
    return None


def platform_for(
    source: str,
    token: str | None = None,
    options: Any = None,
    name: str | None = None,
) -> Platform:
    """Return the platform implementation for source."""

    host, owner, repo = layout.parse_source(source)
    ref = RepoRef(host=host, owner=owner, repo=repo, source=source)

    chosen = (name or detect_platform(host) or "").strip().lower()
    if name and chosen not in PLATFORMS:
        raise ConfigError(
            f'unknown platform "{name}" (choose one of: {", ".join(PLATFORM_NAMES)})'
        )
    if not chosen or not host:
        return UnsupportedPlatform(ref, token, options)

    return PLATFORMS[chosen](ref, token, options)


__all__ = [
    "KIND_ASSET",
    "KIND_SOURCE",
    "PLATFORMS",
    "PLATFORM_NAMES",
    "Platform",
    "RepoRef",
    "SOURCE_SUFFIXES",
    "UnsupportedPlatform",
    "detect_platform",
    "make_asset",
    "make_release",
    "normalize_release",
    "platform_for",
    "source_asset_name",
]
