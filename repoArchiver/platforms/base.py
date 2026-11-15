"""Platform abstraction for release metadata."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .. import http
from ..errors import warn
from ..i18n import tr
from ..selection import KIND_ASSET, KIND_SOURCE

SOURCE_SUFFIXES = ("zip", "tar.gz")


@dataclass(frozen=True)
class RepoRef:
    """A parsed repository source."""

    host: str
    owner: str
    repo: str
    source: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def safe_name(value: str, fallback: str = "source") -> str:
    """Filesystem-safe file name component."""

    from ..layout import safe_component

    return safe_component(value, fallback)


def source_asset_name(repo_name: str, tag: str, suffix: str) -> str:
    """Return the local file name used for an auto-generated source archive."""

    return f"{safe_name(f'{repo_name}-{tag}', 'source')}.{suffix}"


def make_asset(
    name: str,
    url: str | None,
    size: Any = None,
    kind: str = KIND_ASSET,
) -> dict[str, Any]:
    """Build one normalized asset entry."""

    return {
        "name": str(name or ""),
        "url": url,
        "size": size if isinstance(size, int) else None,
        "local_path": None,
        "kind": kind,
    }


def make_release(
    *,
    tag_name: Any = "",
    name: Any = "",
    body: Any = "",
    published_at: Any = None,
    prerelease: Any = False,
    draft: Any = False,
    commit_hash: Any = None,
    zipball_url: Any = None,
    tarball_url: Any = None,
    assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one normalized release entry."""

    return {
        "tag_name": str(tag_name or ""),
        "name": str(name or ""),
        "body": str(body or ""),
        "published_at": published_at if isinstance(published_at, str) else None,
        "prerelease": bool(prerelease),
        "draft": bool(draft),
        "commit_hash": commit_hash if isinstance(commit_hash, str) else None,
        "zipball_url": zipball_url if isinstance(zipball_url, str) else None,
        "tarball_url": tarball_url if isinstance(tarball_url, str) else None,
        "assets": list(assets or []),
    }


class Platform:
    """Base class: knows how to list releases and name source archives."""

    name = "unknown"
    per_page = 100
    max_pages = 50
    api_base = ""

    def __init__(
        self,
        ref: RepoRef,
        token: str | None = None,
        options: http.HttpOptions | None = None,
    ) -> None:
        self.ref = ref
        self.token = token
        self.options = options or http.HttpOptions()


    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


    def auth_headers(self) -> dict[str, str]:
        """Authentication headers; overridden per platform."""

        return {}

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": http.USER_AGENT,
            **self.auth_headers(),
        }

    def get_json(self, url: str) -> tuple[Any, dict[str, str]]:
        return http.get_json(url, self.headers(), self.options)

    def has_next_page(self, payload: Any, headers: dict[str, str]) -> bool:
        """Default pagination signal: a rel="next" link or a full page."""

        link = headers.get("Link") or headers.get("link") or ""
        if 'rel="next"' in link:
            return True
        return isinstance(payload, list) and len(payload) >= self.per_page

    def paginate(self, url_for_page) -> Iterator[Any]:
        """Yield one API payload per page, stopping when the server says so."""

        for page in range(1, self.max_pages + 1):
            payload, headers = self.get_json(url_for_page(page))
            yield payload
            if not self.has_next_page(payload, headers):
                return


    def releases(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def source_assets(
        self,
        release: dict[str, Any],
        tag: str,
    ) -> list[dict[str, Any]]:
        """Return the forge's generated source archives for tag."""

        result: list[dict[str, Any]] = []
        for suffix in SOURCE_SUFFIXES:
            url = self.source_url(tag, suffix)
            if not url:
                continue
            result.append(
                make_asset(
                    source_asset_name(self.ref.repo, tag, suffix),
                    url,
                    kind=KIND_SOURCE,
                )
            )
        return result

    def source_url(self, tag: str, suffix: str) -> str | None:
        """URL of the generated <tag>.<suffix> archive, if derivable."""

        return None

    def fetch(self) -> list[dict[str, Any]]:
        """Fetch releases, converting API failures into warnings.

        Release metadata must never block archiving the code, so every failure
        ends up as a warning plus an empty list.
        """

        try:
            releases = self.releases()
        except http.HttpError as exc:
            if exc.not_found:
                warn(
                    tr(
                        "no {platform} repository {slug} (HTTP 404); "
                        "skipping release metadata",
                        platform=self.name,
                        slug=self.ref.slug,
                    )
                )
            elif exc.unauthorized:
                warn(
                    tr(
                        "{platform} API rejected the request ({detail}); skipping release "
                        "metadata (set a token for private repositories)",
                        platform=self.name,
                        detail=exc,
                    )
                )
            else:
                warn(
                    tr(
                        "{platform} API request failed ({detail}); skipping release metadata",
                        platform=self.name,
                        detail=exc,
                    )
                )
            return []
        return releases


class UnsupportedPlatform(Platform):
    """Placeholder used when the host cannot be mapped to a known forge."""

    name = "unsupported"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.ref.host:
            warn(tr("local repository source, release metadata skipped"))
        else:
            warn(
                tr(
                    "releases API is not implemented for host {host}; skipping release "
                    "metadata (pass --platform to pick one)",
                    host=repr(self.ref.host),
                )
            )
        return []


__all__ = [
    "KIND_ASSET",
    "KIND_SOURCE",
    "Platform",
    "RepoRef",
    "SOURCE_SUFFIXES",
    "UnsupportedPlatform",
    "make_asset",
    "make_release",
    "source_asset_name",
]
