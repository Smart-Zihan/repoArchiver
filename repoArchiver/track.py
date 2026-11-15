"""Follow tracked archives: explicit sync and on-launch trace."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import chain, layout
from .archiver import SCOPE_ALL, SCOPE_CODE, SCOPES, ArchiveRequest, ArchiveResult, run_archive
from .bundle import git
from .cutoff import filter_releases, resolve_cutoff
from .errors import ArchiveError, RepoArchiverError, warn
from .http import HttpOptions
from .i18n import tr
from .platforms import detect_platform
from .refs import RefRules
from .releases import fetch_releases, read_index
from .selection import KIND_ASSET, Selection


@dataclass
class SyncOutcome:
    """What one sync/trace run added."""

    archive_id: str
    scope: str
    added_releases: list[str] = field(default_factory=list)
    added_assets: list[str] = field(default_factory=list)
    added_refs: list[tuple[str, str]] = field(default_factory=list)
    failed_assets: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.added_releases or self.added_assets or self.added_refs or self.failed_assets
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.added_releases:
            parts.append(tr("+{count} release(s)", count=len(self.added_releases)))
        if self.added_assets:
            parts.append(tr("+{count} asset(s)", count=len(self.added_assets)))
        if self.added_refs:
            parts.append(tr("+{count} ref/commit(s)", count=len(self.added_refs)))
        if self.failed_assets:
            parts.append(tr("{count} failed asset(s)", count=len(self.failed_assets)))
        if not parts:
            return tr("up to date")
        return ", ".join(parts)


def request_from_state(
    state: dict[str, Any],
    meta: dict[str, Any],
    tracker: str | None = None,
) -> ArchiveRequest:
    """Rebuild the ArchiveRequest a tracked archive was created with."""

    source = state.get("source") or meta.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ArchiveError(
            "tracker state records no source; re-run `repoArchiver archive -t NAME <url>`"
        )

    scope = state.get("scope") or meta.get("scope") or SCOPE_ALL
    if scope not in SCOPES:
        scope = SCOPE_ALL

    snapshot = state.get("snapshot")
    if not isinstance(snapshot, bool):
        snapshot = meta.get("source_mode") == "snapshot"

    rewrite = state.get("rewrite")
    if not isinstance(rewrite, bool):
        rewrite = meta.get("source_mode") == "rewrite"
    if rewrite:
        snapshot = False

    incremental = state.get("incremental")
    if not isinstance(incremental, bool):
        incremental = bool(meta.get("incremental"))
    if rewrite or snapshot:
        incremental = False

    if scope == "releases":
        snapshot = False
        rewrite = False
        incremental = False

    with_assets = state.get("with_assets")
    if not isinstance(with_assets, bool):
        with_assets = None
    if scope == "code":
        with_assets = None

    raw_selection = state.get("selection")
    if not isinstance(raw_selection, dict):
        raw_selection = meta.get("selection")
    selection = Selection.from_json(raw_selection)

    raw_refs = state.get("refs_rules")
    if not isinstance(raw_refs, dict):
        raw_refs = meta.get("refs_rules")
    refs_rules = RefRules.from_json(raw_refs)

    platform_name = state.get("platform") or meta.get("platform")
    platform = platform_name if isinstance(platform_name, str) else None

    cutoff = meta.get("cutoff")
    cutoff = cutoff if isinstance(cutoff, dict) else {}
    since = cutoff.get("since") if isinstance(cutoff.get("since"), str) else None
    since_tag = cutoff.get("since_tag") if isinstance(cutoff.get("since_tag"), str) else None
    if scope == "code" and not (snapshot or rewrite):
        since = since_tag = None

    label = tracker if tracker is not None else state.get("tracker")
    return ArchiveRequest(
        source=source,
        scope=scope,
        snapshot=snapshot,
        with_assets=with_assets,
        selection=selection,
        since=since,
        since_tag=since_tag,
        tracker=str(label) if isinstance(label, str) and label else None,
        platform=platform,
        refs=refs_rules,
        incremental=incremental,
        rewrite=rewrite,
    )


def tracked_archives(
    config: layout.Config,
    tracker: str | None = None,
) -> list[tuple[str, Path]]:
    """Return (archive_id, path) for every archive with a state.json."""

    root = Path(config.archives_dir)
    if not root.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        try:
            state = layout.read_state(candidate)
        except RepoArchiverError as exc:
            warn(tr('skipping "{archive_id}": {detail}', archive_id=candidate.name, detail=exc))
            continue
        if not state:
            continue
        if tracker is not None and state.get("tracker") != tracker:
            continue
        found.append((candidate.name, candidate))
    return found


def _saved_assets(index: list[dict[str, Any]]) -> set[str]:
    saved: set[str] = set()
    for release in index:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag:
            continue
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            local = asset.get("local_path")
            if isinstance(local, str) and local:
                saved.add(f"{tag}/{asset.get('name')}")
    return saved


def remote_refs(source: str) -> list[tuple[str, str]] | None:
    """Return (commit, ref) for the remote's heads and tags."""

    proc = git(["ls-remote", "--heads", "--tags", source], check=False)
    if proc.returncode != 0:
        return None
    refs: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        commit, _sep, ref = line.partition("\t")
        commit, ref = commit.strip(), ref.strip()
        if not commit or not ref:
            continue
        if not (ref.startswith("refs/heads/") or ref.startswith("refs/tags/")):
            continue
        if ref.endswith("^{}"):
            continue
        refs.append((commit, ref))
    refs.sort(key=lambda item: item[1])
    return refs


def code_changes(
    source: str,
    archived_refs: list[tuple[str, str]],
) -> list[tuple[str, str]] | None:
    """Return remote refs that differ from archived_refs."""

    remote = remote_refs(source)
    if remote is None:
        return None
    known = set(archived_refs)
    return [pair for pair in remote if pair not in known]


def _plan(
    directory: Path,
    request: ArchiveRequest,
    config: layout.Config,
    old_index: list[dict[str, Any]],
    old_refs: list[tuple[str, str]],
) -> SyncOutcome:
    """Report what a real sync would add, without writing anything."""

    outcome = SyncOutcome(archive_id=directory.name, scope=request.scope, dry_run=True)

    if request.wants_code:
        if request.snapshot:
            warn(
                "snapshot archives rewrite every ref, and commit dates are only known "
                "after fetching; code changes are reported before cutoff filtering"
            )
        elif request.rewrite:
            warn(
                "rewritten-history archives rewrite every commit on each sync, so all "
                "refs are reported as pending"
            )
        changes = code_changes(request.source, old_refs)
        if changes is None:
            warn(f'cannot list the remote refs of "{request.source}"; code changes unknown')
        else:
            outcome.added_refs = changes

    if not request.wants_releases:
        return outcome

    host, _owner, _repo = layout.parse_source(request.source)
    options = HttpOptions.from_config(config)
    releases = fetch_releases(
        request.source,
        token=config.token_for(host),
        platform=request.platform,
        options=options,
    )
    cutoff = resolve_cutoff(None, releases, since=request.since, since_tag=request.since_tag)
    if cutoff is not None:
        releases = filter_releases(releases, cutoff)

    known_tags = {
        str(release.get("tag_name"))
        for release in old_index
        if isinstance(release.get("tag_name"), str)
    }
    for release in releases:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag:
            continue
        if not request.selection.allows_release(tag):
            continue
        if tag not in known_tags:
            outcome.added_releases.append(tag)
        asset_dir = directory / "releases" / "assets" / layout.safe_component(tag, "tag")
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = Path(str(asset.get("name") or "")).name
            if not name:
                continue
            if not request.selection.allows_asset(tag, name, str(asset.get("kind") or KIND_ASSET)):
                continue
            size = asset.get("size") if isinstance(asset.get("size"), int) else None
            target = asset_dir / name
            if target.is_file() and (size is None or target.stat().st_size == size):
                continue
            outcome.added_assets.append(f"{tag}/{name}")
    return outcome


def _run_or_skip(
    request: ArchiveRequest,
    config: layout.Config,
    old_refs: list[tuple[str, str]],
) -> ArchiveResult | None:
    """Run the pipeline, or skip it when a code-only archive is already current."""

    if (
        request.scope == SCOPE_CODE
        and not request.snapshot
        and not request.rewrite
        and code_changes(request.source, old_refs) == []
    ):
        return None
    return run_archive(request, config, announce_update=False)


def sync_archive(
    directory: str | Path,
    config: layout.Config,
    *,
    dry_run: bool = False,
    event: str = "sync",
) -> SyncOutcome:
    """Refresh a tracked archive: fetch new releases, download missing assets."""

    archive = Path(directory).expanduser()
    if not archive.is_dir():
        raise ArchiveError(tr('archive not found: "{path}"', path=archive))
    meta = layout.read_meta(archive)
    state = layout.read_state(archive)
    if not state:
        raise ArchiveError(
            tr(
                'archive "{archive_id}" is not tracked '
                "(run `repoArchiver archive -t NAME <url>` first)",
                archive_id=archive.name,
            )
        )

    request = request_from_state(state, meta)
    old_index = read_index(archive / "releases" / "index.json")
    entries = chain.chain_entries(archive, meta)
    old_refs = chain.chain_heads(entries)

    if dry_run:
        return _plan(archive, request, config, old_index, old_refs)

    result = _run_or_skip(request, config, old_refs)
    new_index = read_index(archive / "releases" / "index.json")

    outcome = SyncOutcome(archive_id=archive.name, scope=request.scope)
    known_tags = {
        str(release.get("tag_name"))
        for release in old_index
        if isinstance(release.get("tag_name"), str)
    }
    outcome.added_releases = [
        str(release["tag_name"])
        for release in new_index
        if isinstance(release.get("tag_name"), str) and str(release["tag_name"]) not in known_tags
    ]
    outcome.added_assets = sorted(_saved_assets(new_index) - _saved_assets(old_index))
    outcome.added_refs = (
        [] if result is None else [pair for pair in result.refs if pair not in old_refs]
    )
    outcome.failed_assets = [] if result is None else list(result.asset_failures)

    updated = layout.read_state(archive) or dict(state)
    updated["last_sync_at"] = layout.now_iso()
    layout.write_state(archive, updated)

    if outcome.changed:
        layout.append_history(
            archive,
            {
                "event": event,
                "scope": request.scope,
                "added_releases": outcome.added_releases,
                "added_assets": outcome.added_assets,
                "added_refs": [
                    {"ref": ref, "commit": commit} for commit, ref in outcome.added_refs
                ],
                "failed_assets": outcome.failed_assets,
            },
        )
    return outcome


def trace_archive(
    directory: str | Path,
    config: layout.Config,
) -> SyncOutcome | None:
    """Best-effort refresh of one tracked archive (used on every CLI launch)."""

    archive = Path(directory).expanduser()
    try:
        state = layout.read_state(archive)
        if not state:
            return None
        meta = layout.read_meta(archive)
    except RepoArchiverError:
        return None

    source = state.get("source") or meta.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    try:
        host, _owner, _repo = layout.parse_source(source)
    except RepoArchiverError:
        return None

    if not host:
        return None
    try:
        request = request_from_state(state, meta)
    except RepoArchiverError:
        return None
    if request.wants_releases:
        known = state.get("platform") or meta.get("platform") or detect_platform(host)
        if not known:
            return None

    try:
        return sync_archive(archive, config, event="trace")
    except (RepoArchiverError, OSError) as exc:
        warn(tr('trace of "{archive_id}" failed: {detail}', archive_id=archive.name, detail=exc))
        return None


__all__ = [
    "SyncOutcome",
    "request_from_state",
    "sync_archive",
    "trace_archive",
    "tracked_archives",
]
