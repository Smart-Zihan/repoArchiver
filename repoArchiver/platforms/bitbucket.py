"""Bitbucket Cloud support."""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..selection import KIND_ASSET
from .base import SOURCE_SUFFIXES, Platform, make_asset, make_release

DOWNLOADS_TAG = "downloads"


class BitbucketPlatform(Platform):
    name = "Bitbucket"
    per_page = 100

    @property
    def api_base(self) -> str:
        owner = urllib.parse.quote(self.ref.owner)
        repo = urllib.parse.quote(self.ref.repo)
        return f"https://api.bitbucket.org/2.0/repositories/{owner}/{repo}"

    @property
    def web_base(self) -> str:
        return f"https://bitbucket.org/{self.ref.owner}/{self.ref.repo}"

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def has_next_page(self, payload: Any, headers: dict[str, str]) -> bool:
        return isinstance(payload, dict) and bool(payload.get("next"))

    def collect(self, url: str) -> list[dict[str, Any]]:
        """Follow Bitbucket's next cursor and collect values."""

        items: list[dict[str, Any]] = []
        pages = 0
        while url and pages < self.max_pages:
            payload, _headers = self.get_json(url)
            pages += 1
            if not isinstance(payload, dict):
                break
            items.extend(item for item in payload.get("values") or [] if isinstance(item, dict))
            next_url = payload.get("next")
            url = next_url if isinstance(next_url, str) else ""
        return items

    def releases(self) -> list[dict[str, Any]]:
        releases = [
            self.normalize_tag(tag)
            for tag in self.collect(f"{self.api_base}/refs/tags?pagelen={self.per_page}")
        ]
        downloads = self.download_assets()
        if downloads:
            releases.append(
                make_release(
                    tag_name=DOWNLOADS_TAG,
                    name="Bitbucket downloads",
                    body="Files from the repository's Downloads area.",
                    published_at=None,
                    assets=downloads,
                )
            )
        return releases

    def normalize_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        name = str(tag.get("name") or "")
        target = tag.get("target") if isinstance(tag.get("target"), dict) else {}
        normalized = make_release(
            tag_name=name,
            name=name,
            body=target.get("message"),
            published_at=target.get("date"),
            commit_hash=target.get("hash"),
        )
        normalized["assets"].extend(self.source_assets(normalized, name))
        return normalized

    def download_assets(self) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for item in self.collect(f"{self.api_base}/downloads?pagelen={self.per_page}"):
            name = str(item.get("name") or "")
            links = item.get("links") if isinstance(item.get("links"), dict) else {}
            self_link = links.get("self") if isinstance(links.get("self"), dict) else {}
            url = self_link.get("href")
            if name and isinstance(url, str) and url:
                assets.append(make_asset(name, url, item.get("size"), KIND_ASSET))
        return assets

    def source_url(self, tag: str, suffix: str) -> str | None:
        if suffix not in SOURCE_SUFFIXES or not tag:
            return None
        ref = urllib.parse.quote(tag, safe="/")
        return f"{self.web_base}/get/{ref}.{suffix}"


__all__ = ["DOWNLOADS_TAG", "BitbucketPlatform"]
