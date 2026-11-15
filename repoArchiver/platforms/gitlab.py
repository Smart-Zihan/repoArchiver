"""GitLab (gitlab.com and self-hosted) release support."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..selection import KIND_ASSET, KIND_SOURCE
from .base import Platform, make_asset, make_release, source_asset_name

_SOURCE_FORMATS = ("zip", "tar.gz", "tar.bz2", "tar")


class GitLabPlatform(Platform):
    name = "GitLab"
    per_page = 100

    @property
    def api_base(self) -> str:
        return f"https://{self.ref.host}/api/v4"

    @property
    def project_id(self) -> str:
        return urllib.parse.quote(f"{self.ref.owner}/{self.ref.repo}", safe="")

    def auth_headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token} if self.token else {}

    def has_next_page(self, payload: Any, headers: dict[str, str]) -> bool:
        next_page = headers.get("X-Next-Page") or headers.get("x-next-page") or ""
        return bool(str(next_page).strip())

    def releases(self) -> list[dict[str, Any]]:
        def url_for_page(page: int) -> str:
            query = urllib.parse.urlencode({"per_page": self.per_page, "page": page})
            return f"{self.api_base}/projects/{self.project_id}/releases?{query}"

        collected: list[dict[str, Any]] = []
        for payload in self.paginate(url_for_page):
            if not isinstance(payload, list):
                break
            collected.extend(
                self.normalize(item) for item in payload if isinstance(item, dict)
            )
        return collected

    def normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        tag = str(item.get("tag_name") or "")
        assets_payload = item.get("assets") if isinstance(item.get("assets"), dict) else {}
        assets: list[dict[str, Any]] = []
        for link in assets_payload.get("links") or []:
            if not isinstance(link, dict):
                continue
            name = str(link.get("name") or "")
            url = link.get("direct_asset_url") or link.get("url")
            assets.append(make_asset(name, url, kind=KIND_ASSET))
        for source in assets_payload.get("sources") or []:
            if not isinstance(source, dict):
                continue
            fmt = str(source.get("format") or "").strip()
            url = source.get("url")
            if not fmt or not isinstance(url, str) or not url:
                continue
            if fmt not in _SOURCE_FORMATS:
                continue
            assets.append(
                make_asset(source_asset_name(self.ref.repo, tag, fmt), url, kind=KIND_SOURCE)
            )

        commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
        commit_hash = commit.get("id") or item.get("commit_id")
        return make_release(
            tag_name=tag,
            name=item.get("name"),
            body=item.get("description"),
            published_at=item.get("released_at") or item.get("created_at"),
            prerelease=item.get("upcoming_release"),
            draft=False,
            commit_hash=commit_hash,
            assets=assets,
        )


__all__ = ["GitLabPlatform"]
