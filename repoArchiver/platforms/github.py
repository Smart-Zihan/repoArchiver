"""GitHub (and GitHub Enterprise) release support."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..selection import KIND_ASSET, KIND_SOURCE
from .base import SOURCE_SUFFIXES, Platform, make_asset, make_release, source_asset_name

GITHUB_HOSTS = {"github.com", "www.github.com"}
GITHUB_API = "https://api.github.com"

_SOURCE_FIELDS = (("zip", "zipball_url"), ("tar.gz", "tarball_url"))


class GitHubPlatform(Platform):
    name = "GitHub"
    hosts = GITHUB_HOSTS
    per_page = 100

    @property
    def api_base(self) -> str:
        """api.github.com for github.com, /api/v3 for Enterprise."""

        if self.ref.host in GITHUB_HOSTS:
            return GITHUB_API
        return f"https://{self.ref.host}/api/v3"

    def auth_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def releases(self) -> list[dict[str, Any]]:
        owner = urllib.parse.quote(self.ref.owner)
        repo = urllib.parse.quote(self.ref.repo)

        def url_for_page(page: int) -> str:
            query = urllib.parse.urlencode({"per_page": self.per_page, "page": page})
            return f"{self.api_base}/repos/{owner}/{repo}/releases?{query}"

        collected: list[dict[str, Any]] = []
        for payload in self.paginate(url_for_page):
            if not isinstance(payload, list):
                break
            collected.extend(
                normalize_release(
                    item,
                    repo_name=self.ref.repo,
                    owner=self.ref.owner,
                    source_for=self.source_url,
                )
                for item in payload
                if isinstance(item, dict)
            )
        return collected

    def source_url(self, tag: str, suffix: str) -> str | None:
        """codeload URL of a generated source archive."""

        return codeload_url(self.ref.owner, self.ref.repo, tag, suffix)


def codeload_url(owner: str, repo_name: str, tag: str, suffix: str) -> str | None:
    """Build a codeload.github.com source-archive URL."""

    if suffix not in SOURCE_SUFFIXES or not tag or not owner or not repo_name:
        return None
    ref = urllib.parse.quote(tag, safe="/")
    return f"https://codeload.github.com/{owner}/{repo_name}/{suffix}/refs/tags/{ref}"


def normalize_release(
    release: dict[str, Any],
    repo_name: str = "",
    owner: str = "",
    source_for=None,
) -> dict[str, Any]:
    """Turn one GitHub API release into the stored shape."""

    if source_for is None and owner and repo_name:
        source_for = lambda tag, suffix: codeload_url(owner, repo_name, tag, suffix)  # noqa: E731

    assets = []
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = asset.get("browser_download_url") or asset.get("url")
        assets.append(make_asset(name, url, asset.get("size"), KIND_ASSET))
    normalized = make_release(
        tag_name=release.get("tag_name"),
        name=release.get("name"),
        body=release.get("body"),
        published_at=release.get("published_at"),
        prerelease=release.get("prerelease"),
        draft=release.get("draft"),
        zipball_url=release.get("zipball_url"),
        tarball_url=release.get("tarball_url"),
        assets=assets,
    )
    normalized["assets"].extend(
        source_assets_for(normalized, repo_name, owner, source_for)
    )
    return normalized


def source_assets_for(
    release: dict[str, Any],
    repo_name: str,
    owner: str,
    source_for,
) -> list[dict[str, Any]]:
    """Synthesize the source archives GitHub serves outside assets[]."""

    tag = str(release.get("tag_name") or "").strip()
    if not tag or not repo_name:
        return []
    taken = {str(asset.get("name")) for asset in release.get("assets") or []}
    result: list[dict[str, Any]] = []
    for suffix, key in _SOURCE_FIELDS:
        url = source_for(tag, suffix) if source_for else None
        if not url:
            url = release.get(key)
        if not isinstance(url, str) or not url:
            continue
        name = source_asset_name(repo_name, tag, suffix)
        if name in taken:
            name = f"{source_asset_name(repo_name, tag, suffix)[:-len(suffix) - 1]}-source.{suffix}"
        taken.add(name)
        result.append(make_asset(name, url, kind=KIND_SOURCE))
    return result


__all__ = ["GITHUB_API", "GITHUB_HOSTS", "GitHubPlatform", "normalize_release", "source_assets_for"]
