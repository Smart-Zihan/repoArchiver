"""Selection rules for releases and release assets."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

KIND_ASSET = "asset"
KIND_SOURCE = "source"


def _globs(values: Any) -> tuple[str, ...]:
    """Normalize a config/CLI value into a tuple of lowercase patterns."""

    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    try:
        items = list(values)
    except TypeError:
        return ()
    patterns: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            patterns.append(item.strip().lower())
    return tuple(patterns)


@dataclass(frozen=True)
class Selection:
    """Glob-based include/exclude rules for releases and their assets."""

    releases: tuple[str, ...] = field(default=())
    skip_releases: tuple[str, ...] = field(default=())
    assets: tuple[str, ...] = field(default=())
    skip_assets: tuple[str, ...] = field(default=())
    source_archives: bool = True

    def __post_init__(self) -> None:
        for name in ("releases", "skip_releases", "assets", "skip_assets"):
            object.__setattr__(self, name, _globs(getattr(self, name)))

    @staticmethod
    def _hit(patterns: tuple[str, ...], *values: str) -> bool:
        return any(
            fnmatch.fnmatchcase(value.lower(), pattern)
            for pattern in patterns
            for value in values
        )

    def allows_release(self, tag: str) -> bool:
        """Return whether release tag is in scope for downloading."""

        tag = (tag or "").strip()
        if not tag:
            return False
        if self.releases and not self._hit(self.releases, tag):
            return False
        return not (self.skip_releases and self._hit(self.skip_releases, tag))

    def allows_asset(self, tag: str, name: str, kind: str = KIND_ASSET) -> bool:
        """Return whether asset name of release tag is in scope."""

        if kind == KIND_SOURCE and not self.source_archives:
            return False
        name = (name or "").strip()
        if not name:
            return False
        pair = f"{tag}/{name}" if tag else name
        if self.assets and not self._hit(self.assets, name, pair):
            return False
        return not (self.skip_assets and self._hit(self.skip_assets, name, pair))

    def is_default(self) -> bool:
        """True when the selection keeps everything."""

        return (
            not self.releases
            and not self.skip_releases
            and not self.assets
            and not self.skip_assets
            and self.source_archives
        )

    def to_json(self) -> dict[str, Any]:
        """Serialize to the shape stored in meta.json and state.json."""

        return {
            "releases": list(self.releases),
            "skip_releases": list(self.skip_releases),
            "assets": list(self.assets),
            "skip_assets": list(self.skip_assets),
            "source_archives": self.source_archives,
        }

    @classmethod
    def from_json(cls, data: Any) -> Selection:
        """Rebuild a selection from persisted JSON; invalid parts are ignored."""

        if not isinstance(data, dict):
            return cls()

        def items(key: str) -> tuple[str, ...]:
            value = data.get(key)
            return tuple(value) if isinstance(value, list) else ()

        source = data.get("source_archives", True)
        return cls(
            releases=items("releases"),
            skip_releases=items("skip_releases"),
            assets=items("assets"),
            skip_assets=items("skip_assets"),
            source_archives=source if isinstance(source, bool) else True,
        )

    def describe(self) -> str:
        """Human-readable one-liner for inspect output."""

        parts: list[str] = []
        if self.releases:
            parts.append("releases=" + ",".join(self.releases))
        if self.skip_releases:
            parts.append("skip-releases=" + ",".join(self.skip_releases))
        if self.assets:
            parts.append("assets=" + ",".join(self.assets))
        if self.skip_assets:
            parts.append("skip-assets=" + ",".join(self.skip_assets))
        if not self.source_archives:
            parts.append("no-source")
        return "; ".join(parts) if parts else "everything"


__all__ = ["KIND_ASSET", "KIND_SOURCE", "Selection"]
