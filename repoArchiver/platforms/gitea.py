"""Gitea / Forgejo release support (including Codeberg)."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..selection import KIND_ASSET
from .base import SOURCE_SUFFIXES, Platform, make_asset, make_release


class GiteaPlatform(Platform):
    name = "Gitea"
    per_page = 50

    @property
    def api_base(self) -> str:
        return f"https://{self.ref.host}/api/v1"

    @property
    def web_base(self) -> str:
        return f"https://{self.ref.host}/{self.ref.owner}/{self.ref.repo}"

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"token {self.token}"} if self.token else {}

    def releases(self) -> list[dict[str, Any]]:
        owner = urllib.parse.quote(self.ref.owner)
        repo = urllib.parse.quote(self.ref.repo)

        def url_for_page(page: int) -> str:
            query = urllib.parse.urlencode({"limit": self.per_page, "page": page})
            return f"{self.api_base}/repos/{owner}/{repo}/releases?{query}"

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
        assets = []
        for asset in item.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            url = asset.get("browser_download_url")
            if not isinstance(url, str) or not url.startswith("http"):
                url = None
            assets.append(make_asset(name, url, asset.get("size"), KIND_ASSET))
        normalized = make_release(
            tag_name=tag,
            name=item.get("name"),
            body=item.get("body"),
            published_at=item.get("published_at") or item.get("created_at"),
            prerelease=item.get("prerelease"),
            draft=item.get("draft"),
            zipball_url=item.get("zipball_url"),
            tarball_url=item.get("tarball_url"),
            assets=assets,
        )
        normalized["assets"].extend(self.source_assets(normalized, tag))
        return normalized

    def source_url(self, tag: str, suffix: str) -> str | None:
        """Gitea serves <repo>/archive/<ref>.zip|.tar.gz for any ref."""

        if suffix not in SOURCE_SUFFIXES or not tag:
            return None
        ref = urllib.parse.quote(tag, safe="/")
        return f"{self.web_base}/archive/{ref}.{suffix}"


__all__ = ["GiteaPlatform"]
