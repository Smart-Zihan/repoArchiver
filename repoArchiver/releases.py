"""Release metadata and release-asset downloads."""

from __future__ import annotations

import json
import os
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import http, progress
from .errors import ArchiveError, warn
from .i18n import tr
from .layout import safe_component, write_json_atomic
from .platforms import platform_for
from .platforms.base import (
    SOURCE_SUFFIXES,
    make_asset,
    make_release,
    source_asset_name,
)
from .platforms.github import GITHUB_HOSTS, source_assets_for
from .platforms.github import normalize_release as normalize_release
from .selection import KIND_ASSET, KIND_SOURCE, Selection

USER_AGENT = http.USER_AGENT
HTTP_TIMEOUT = http.DEFAULT_TIMEOUT
MAX_PAGES = 50
PER_PAGE = 100


def source_asset_url(owner: str, repo_name: str, tag: str, suffix: str) -> str:
    """Return the codeload URL of a generated GitHub source archive."""

    import urllib.parse

    ref = urllib.parse.quote(tag, safe="/")
    return f"https://codeload.github.com/{owner}/{repo_name}/{suffix}/refs/tags/{ref}"


def fetch_releases(
    src: str,
    token: str | None = None,
    *,
    platform: str | None = None,
    options: http.HttpOptions | None = None,
) -> list[dict[str, Any]]:
    """Fetch and normalize releases for src."""

    impl = platform_for(src, token, options, platform)
    return impl.fetch()


def asset_headers(
    src: str,
    token: str | None = None,
    platform: str | None = None,
) -> dict[str, str]:
    """Authentication headers a platform needs when downloading its assets."""

    impl = platform_for(src, token, None, platform)
    return impl.auth_headers()


def source_assets(
    release: dict[str, Any],
    repo_name: str,
    tag: str | None = None,
    owner: str = "",
    taken: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the generated source archives of a GitHub-style release."""

    resolved_tag = str(tag or release.get("tag_name") or "").strip()

    def source_for(name: str, suffix: str) -> str | None:
        if owner:
            return source_asset_url(owner, repo_name, name, suffix)
        field = {"zip": "zipball_url", "tar.gz": "tarball_url"}.get(suffix)
        value = release.get(field) if field else None
        return value if isinstance(value, str) and value else None

    prepared = dict(release)
    prepared["tag_name"] = resolved_tag
    if taken:
        prepared["assets"] = [{"name": name} for name in taken]
    return source_assets_for(prepared, repo_name, owner, source_for)


def write_index(releases: list[dict[str, Any]], path: str | os.PathLike[str]) -> None:
    """Atomically write releases/index.json."""

    write_json_atomic(path, releases)


def read_index(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read releases/index.json; a missing file yields an empty list."""

    index_path = Path(path)
    if not index_path.is_file():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArchiveError(f'cannot read release index "{index_path}": {exc}') from exc
    except json.JSONDecodeError as exc:
        raise ArchiveError(f'release index "{index_path}" is not valid JSON: {exc}') from exc
    if not isinstance(data, list):
        raise ArchiveError(f'release index "{index_path}" must contain a JSON array')
    return [item for item in data if isinstance(item, dict)]


def find_release(releases: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    """Find a release whose tag matches ref (full ref, leaf, or tag name)."""

    candidates = {ref}
    leaf = ref.rsplit("/", 1)[-1]
    candidates.add(leaf)
    if ref.startswith("refs/tags/"):
        candidates.add(ref[len("refs/tags/") :])
    for release in releases:
        tag = release.get("tag_name")
        if isinstance(tag, str) and tag in candidates:
            return release
    return None


def download_asset(
    url: str,
    target: str | os.PathLike[str],
    token: str | None = None,
    expected_size: int | None = None,
    *,
    headers: dict[str, str] | None = None,
    options: http.HttpOptions | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Download one asset to target atomically; return the written path."""

    merged = dict(headers or {})
    supplied = {name.lower() for name in merged}
    if token and not supplied & {"authorization", "private-token"}:
        merged["Authorization"] = f"Bearer {token}"
    return http.download(url, target, merged, options, expected_size, on_progress)


def download_assets(
    index: list[dict[str, Any]],
    tag: str,
    dest_dir: str | os.PathLike[str],
    token: str | None = None,
    *,
    repo_name: str = "",
    selection: Selection | None = None,
    headers: dict[str, str] | None = None,
    options: http.HttpOptions | None = None,
) -> list[str]:
    """Download the in-scope assets of release tag into dest_dir.

    local_path is recorded back into the index entry. Returns the names that
    failed.
    """

    rules = selection or Selection()
    release = find_release(index, tag)
    if release is None:
        warn(tr('no release with tag "{tag}", no assets to download', tag=tag))
        return []

    if not release.get("assets") and repo_name:
        release["assets"] = source_assets(release, repo_name, tag)

    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    reporter = progress.progress()
    failures: list[str] = []
    for asset in release.get("assets") or []:
        name = Path(str(asset.get("name") or "")).name
        if not name:
            failures.append(str(asset.get("name") or "<unnamed>"))
            warn(
                tr(
                    "failed to download asset {name}: invalid asset name",
                    name=repr(asset.get("name")),
                )
            )
            continue
        if not rules.allows_asset(tag, name, str(asset.get("kind") or KIND_ASSET)):
            continue
        target = destination / name
        expected_size = asset.get("size") if isinstance(asset.get("size"), int) else None
        if target.is_file() and (expected_size is None or target.stat().st_size == expected_size):
            asset["local_path"] = str(target)
            continue
        url = asset.get("url")
        if not isinstance(url, str) or not url:
            failures.append(name)
            warn(
                tr(
                    "failed to download asset {name}: no download URL in release metadata",
                    name=name,
                )
            )
            continue
        bar = None
        if reporter is not None and reporter.interactive:
            label = f"{tag}/{name}"

            def bar(written: int, total: int | None, _label: str = label) -> None:
                reporter.download(_label, written, total)
        try:
            download_asset(
                url,
                target,
                token=token,
                expected_size=expected_size,
                headers=headers,
                options=options,
                on_progress=bar,
            )
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            failures.append(name)
            warn(f"failed to download asset {name}: {exc}")
            continue
        except ArchiveError as exc:
            failures.append(name)
            warn(f"failed to download asset {name}: {exc}")
            continue
        asset["local_path"] = str(target)
        size = target.stat().st_size
        if reporter is not None:
            reporter.break_line()
        print(f"downloaded asset {tag}/{name} ({size} bytes)")
    return failures


def count_assets(index: list[dict[str, Any]]) -> dict[str, int]:
    """Count indexed assets by total, saved and source."""

    total = saved = sources = 0
    for release in index:
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            total += 1
            if isinstance(asset.get("local_path"), str) and asset["local_path"]:
                saved += 1
            if asset.get("kind") == KIND_SOURCE:
                sources += 1
    return {"total": total, "saved": saved, "source": sources}


__all__ = [
    "GITHUB_HOSTS",
    "KIND_ASSET",
    "KIND_SOURCE",
    "MAX_PAGES",
    "PER_PAGE",
    "SOURCE_SUFFIXES",
    "asset_headers",
    "count_assets",
    "download_asset",
    "download_assets",
    "fetch_releases",
    "find_release",
    "make_asset",
    "make_release",
    "read_index",
    "safe_component",
    "source_assets",
    "source_asset_name",
    "source_asset_url",
    "write_index",
]
