"""The archive pipeline shared by the archive and sync commands."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import chain, layout, progress
from .bundle import create_bundle, git, list_heads, mirror_source, rev_parse_commit
from .cutoff import Cutoff, build_snapshot_bundle, filter_releases, resolve_cutoff, select_refs
from .errors import ArchiveError, warn
from .http import HttpOptions
from .i18n import tr
from .platforms import detect_platform
from .refs import ORPHAN_PREFIX, RefRules, bundle_refs_args, select_for_bundle
from .releases import (
    asset_headers,
    count_assets,
    download_assets,
    fetch_releases,
    read_index,
    write_index,
)
from .rewrite import RewriteResult, build_rewritten_bundle
from .selection import Selection

SCOPE_ALL = "all"
SCOPE_CODE = "code"
SCOPE_RELEASES = "releases"
SCOPES = (SCOPE_ALL, SCOPE_CODE, SCOPE_RELEASES)

BASE_BUNDLE = chain.BASE_NAME


@dataclass(frozen=True)
class ArchiveRequest:
    """What to archive, and how."""

    source: str
    scope: str = SCOPE_ALL
    snapshot: bool = False
    with_assets: bool | None = None
    selection: Selection = field(default_factory=Selection)
    since: str | None = None
    since_tag: str | None = None
    tracker: str | None = None
    platform: str | None = None
    refs: RefRules = field(default_factory=RefRules)
    incremental: bool = False
    rewrite: bool = False
    out_dir: str | None = None

    @property
    def wants_code(self) -> bool:
        return self.scope in (SCOPE_ALL, SCOPE_CODE)

    @property
    def wants_releases(self) -> bool:
        return self.scope in (SCOPE_ALL, SCOPE_RELEASES)

    def assets_enabled(self) -> bool:
        """Resolve the effective asset-download switch."""

        if not self.wants_releases:
            return False
        if self.with_assets is None:
            return self.scope == SCOPE_RELEASES
        return bool(self.with_assets)

    def validate(self) -> None:
        if self.scope not in SCOPES:
            raise ArchiveError(f'unknown scope "{self.scope}"')
        if self.scope == SCOPE_RELEASES and self.snapshot:
            raise ArchiveError(
                tr("--snapshot stores code, so it cannot be combined with --only-releases")
            )
        if self.scope == SCOPE_RELEASES and (self.rewrite or self.incremental):
            raise ArchiveError(
                tr(
                    "--rewrite-history/--incremental store code, so they cannot be combined "
                    "with --only-releases"
                )
            )
        if self.scope == SCOPE_CODE and self.with_assets:
            raise ArchiveError(
                tr("--with-assets stores releases, so it cannot be combined with --only-code")
            )
        if self.snapshot and self.rewrite:
            raise ArchiveError(tr("use either --snapshot or --rewrite-history, not both"))
        if self.rewrite and self.incremental:
            raise ArchiveError(
                tr("--rewrite-history rewrites every commit, so --incremental cannot help")
            )
        if self.rewrite and not (self.since or self.since_tag):
            raise ArchiveError(
                tr("--rewrite-history needs a cutoff: pass --since or --since-tag")
            )


@dataclass
class ArchiveResult:
    """What run_archive produced."""

    archive_id: str
    out_dir: Path
    scope: str
    snapshot: bool
    with_assets: bool
    bundle_path: Path | None
    refs: list[tuple[str, str]]
    releases: list[dict[str, Any]]
    cutoff: Cutoff | None
    asset_failures: list[str] = field(default_factory=list)
    incremental: bool = False
    rewritten: RewriteResult | None = None
    unchanged: bool = False
    orphans: int = 0

    @property
    def assets_saved(self) -> int:
        return count_assets(self.releases)["saved"]


@dataclass
class _CodeResult:
    """Outcome of the code half of the pipeline."""

    bundle: Path | None
    bundle_name: str | None
    refs: list[tuple[str, str]]
    chain: list[dict[str, Any]] | None = None
    unchanged: bool = False
    rewritten: RewriteResult | None = None
    orphans: int = 0


def _merge_index(
    old_index: list[dict[str, Any]],
    new_index: list[dict[str, Any]],
    out_dir: Path,
) -> list[dict[str, Any]]:
    """Carry commit_hash and a still-valid local_path over to a refresh."""

    previous: dict[str, dict[str, Any]] = {}
    for release in old_index:
        tag = release.get("tag_name")
        if isinstance(tag, str) and tag:
            previous[tag] = release

    for release in new_index:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or tag not in previous:
            continue
        old_release = previous[tag]
        if not release.get("commit_hash") and isinstance(old_release.get("commit_hash"), str):
            release["commit_hash"] = old_release["commit_hash"]
        old_assets = {
            str(asset.get("name")): asset
            for asset in old_release.get("assets") or []
            if isinstance(asset, dict) and asset.get("name")
        }
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            prior = old_assets.get(str(asset.get("name")))
            if prior is None:
                continue
            if asset.get("size") is None and isinstance(prior.get("size"), int):
                asset["size"] = prior["size"]
            local = prior.get("local_path")
            if isinstance(local, str) and local:
                located = Path(local)
                if not located.is_absolute():
                    located = out_dir / located
                if located.is_file():
                    asset["local_path"] = local
    return new_index


def _write_state(
    out_dir: Path,
    request: ArchiveRequest,
    meta: dict[str, Any],
    with_assets: bool,
    platform_name: str | None,
) -> None:
    """Write or refresh state.json for a tracked archive."""

    previous = layout.read_state(out_dir) or {}
    state: dict[str, Any] = {
        "tracker": request.tracker,
        "source": request.source,
        "scope": request.scope,
        "snapshot": request.snapshot,
        "with_assets": with_assets,
        "archived_at": meta["archived_at"],
        "tracked_at": previous.get("tracked_at") or meta["archived_at"],
        "last_sync_at": previous.get("last_sync_at"),
        "refs_count": meta["refs_count"],
        "release_count": meta["release_count"],
    }
    if not request.selection.is_default():
        state["selection"] = request.selection.to_json()
    if not request.refs.is_default():
        state["refs_rules"] = request.refs.to_json()
    if request.incremental:
        state["incremental"] = True
    if request.rewrite:
        state["rewrite"] = True
    if platform_name:
        state["platform"] = platform_name
    layout.write_state(out_dir, state)


def _base_chain() -> list[dict[str, Any]]:
    return [{"path": BASE_BUNDLE, "kind": "base", "created_at": layout.now_iso()}]


def _archive_code(
    repo: Path,
    out_dir: Path,
    request: ArchiveRequest,
    cutoff: Cutoff | None,
    old_meta: dict[str, Any],
) -> _CodeResult:
    """Create or extend the code bundle according to the request."""

    bundle_path = out_dir / BASE_BUNDLE

    if request.rewrite:
        assert cutoff is not None
        selected = request.refs.apply(select_refs(repo, cutoff))
        result = build_rewritten_bundle(str(repo), str(bundle_path), selected, cutoff)
        return _CodeResult(
            bundle=bundle_path,
            bundle_name=bundle_path.name,
            refs=list_heads(bundle_path),
            chain=_base_chain(),
            rewritten=result,
        )

    if request.snapshot:
        selected = request.refs.apply(select_refs(repo, cutoff))
        if not selected:
            raise ArchiveError(
                tr(
                    "no branches or tags at/after the cutoff ({cutoff})",
                    cutoff=cutoff.describe(),
                )
                if cutoff is not None
                else tr("repository has no branches or tags to snapshot")
            )
        build_snapshot_bundle(repo, bundle_path, selected)
        return _CodeResult(
            bundle=bundle_path,
            bundle_name=bundle_path.name,
            refs=list_heads(bundle_path),
            chain=_base_chain(),
        )

    selected = select_for_bundle(str(repo), request.refs)
    orphans = sum(1 for _commit, ref in selected if ref.startswith(ORPHAN_PREFIX))
    names = bundle_refs_args(selected)

    entries = chain.chain_entries(out_dir, old_meta)
    if request.incremental and entries and entries[0].path.is_file():
        current = chain.chain_heads(entries)
        if current == selected:
            old_chain = old_meta.get("bundle_chain")
            kept_chain = (
                [dict(item) for item in old_chain if isinstance(item, dict)]
                if isinstance(old_chain, list)
                else []
            )
            return _CodeResult(
                bundle=entries[0].path,
                bundle_name=str(old_meta.get("bundle") or entries[0].name),
                refs=current,
                chain=kept_chain or None,
                unchanged=True,
                orphans=orphans,
            )
        new_bundle = chain.create_incremental(str(repo), str(out_dir), entries, names)
        if new_bundle is not None:
            extended = entries + [chain.BundleEntry(new_bundle, "incremental")]
            heads = chain.chain_heads(extended)
            if heads == selected:
                return _CodeResult(
                    bundle=entries[0].path,
                    bundle_name=entries[0].name,
                    refs=heads,
                    chain=chain.append_entry(old_meta, new_bundle.name),
                    orphans=orphans,
                )
            new_bundle.unlink(missing_ok=True)
            warn(
                tr(
                    "refs changed in a way an increment cannot express; "
                    "rebuilding the full bundle"
                )
            )

    create_bundle(repo, bundle_path, names)
    old_chain = chain.chain_entries(out_dir, old_meta)
    if len(old_chain) > 1:
        chain.remove_incrementals(out_dir, keep_base=True)
    return _CodeResult(
        bundle=bundle_path,
        bundle_name=bundle_path.name,
        refs=list_heads(bundle_path),
        chain=_base_chain() if (request.incremental or len(old_chain) > 1) else None,
        orphans=orphans,
    )


def run_archive(
    request: ArchiveRequest,
    config: layout.Config,
    *,
    announce_update: bool = True,
) -> ArchiveResult:
    """Create or refresh the archive described by request."""

    request.validate()
    archive_id = layout.archive_id_from_url(request.source)
    out_dir = (
        Path(request.out_dir).expanduser()
        if request.out_dir
        else layout.archive_dir(config.archives_dir, archive_id)
    )

    if out_dir.exists() and not out_dir.is_dir():
        raise ArchiveError(
            tr('archive path "{path}" exists and is not a directory', path=out_dir)
        )
    updating = (out_dir / "meta.json").is_file()
    if updating and announce_update:
        warn(tr('updating existing archive "{archive_id}"', archive_id=archive_id))

    index_path = out_dir / "releases" / "index.json"
    old_index = read_index(index_path)
    old_meta = layout.read_meta(out_dir) if updating else {}

    host, _owner, repo_name = layout.parse_source(request.source)
    options = HttpOptions.from_config(config)
    platform_name = request.platform or config.platform or (
        detect_platform(host) if request.wants_releases and host else None
    )
    token = config.token_for(host)
    snapshot = request.snapshot
    with_assets = request.assets_enabled()
    cutoff: Cutoff | None = None
    releases: list[dict[str, Any]] = []
    code: _CodeResult | None = None
    repo: Path | None = None
    mirror: Path | None = None
    mirror_root: Path | None = None

    try:
        if request.wants_code:
            if host or snapshot or request.rewrite or request.refs.keep_orphans:
                config.archives_dir.mkdir(parents=True, exist_ok=True)
                mirror_root = config.archives_dir / ".mirrors"
                mirror_root.mkdir(exist_ok=True)
                mirror = mirror_root / f"{archive_id}-{uuid.uuid4().hex[:8]}"
                mirror_source(
                    request.source,
                    mirror,
                    copy_objects=request.refs.keep_orphans and not host,
                )
                repo = mirror
            else:
                local = Path(request.source).expanduser().resolve()
                if not local.is_dir():
                    raise ArchiveError(tr('local repository not found: "{path}"', path=local))
                repo = local

            refs_proc = git(["for-each-ref", "--format=%(refname)"], cwd=repo)
            if not refs_proc.stdout.strip():
                raise ArchiveError(
                    tr(
                        'repository "{source}" has no refs; nothing to archive',
                        source=request.source,
                    )
                )

            if not request.wants_releases and (request.since or request.since_tag):
                cutoff = resolve_cutoff(
                    repo, [], since=request.since, since_tag=request.since_tag
                )
                if cutoff is not None and not (snapshot or request.rewrite):
                    warn(
                        tr(
                            "cutoff trims code only with --snapshot or --rewrite-history; "
                            "this code-only archive keeps the full history"
                        )
                    )
                    cutoff = None

        if request.wants_releases:
            progress.stage(tr("fetching release metadata from {source}", source=request.source))
            releases = fetch_releases(
                request.source,
                token=token,
                platform=platform_name,
                options=options,
            )
            cutoff = resolve_cutoff(
                repo,
                releases,
                since=request.since,
                since_tag=request.since_tag,
            )
            if cutoff is not None:
                before = len(releases)
                releases = filter_releases(releases, cutoff)
                if before != len(releases):
                    print(
                        tr(
                            "cutoff {cutoff}: kept {kept} of {total} release(s)",
                            cutoff=cutoff.describe(),
                            kept=len(releases),
                            total=before,
                        )
                    )
                if request.wants_code and not (snapshot or request.rewrite):
                    warn(
                        tr(
                            "cutoff filters release metadata only; repository history is kept "
                            "in full (add --snapshot or --rewrite-history to trim the code)"
                        )
                    )
            if repo is not None:
                for release in releases:
                    tag = release.get("tag_name")
                    if isinstance(tag, str) and tag:
                        commit = rev_parse_commit(repo, f"refs/tags/{tag}")
                        if commit:
                            release["commit_hash"] = commit

        if request.wants_code and repo is not None:
            progress.stage(tr("archiving code from {source}", source=request.source))
            code = _archive_code(repo, out_dir, request, cutoff, old_meta)
    finally:
        if mirror is not None:
            shutil.rmtree(mirror, ignore_errors=True)
        if mirror_root is not None:
            try:
                mirror_root.rmdir()
            except OSError:
                pass

    asset_failures: list[str] = []
    if request.wants_releases:
        if old_index:
            releases = _merge_index(old_index, releases, out_dir)
        write_index(releases, index_path)

        if with_assets:
            headers = asset_headers(request.source, token=token, platform=platform_name)
            wanted = [
                str(release.get("tag_name"))
                for release in releases
                if isinstance(release.get("tag_name"), str)
                and request.selection.allows_release(str(release.get("tag_name")))
            ]
            if wanted:
                progress.stage(
                    tr("downloading assets of {count} release(s)", count=len(wanted))
                )
            for release in releases:
                tag = release.get("tag_name")
                if not isinstance(tag, str) or not tag:
                    continue
                if not request.selection.allows_release(tag):
                    continue
                asset_dir = out_dir / "releases" / "assets" / layout.safe_component(tag, "tag")
                asset_failures += download_assets(
                    releases,
                    tag,
                    asset_dir,
                    token=token,
                    repo_name=repo_name,
                    selection=request.selection,
                    headers=headers,
                    options=options,
                )
            write_index(releases, index_path)
    else:
        if old_index:
            releases = old_index
            warn(
                tr(
                    "code-only scope: existing release metadata is kept but not refreshed "
                    '(delete "releases/" to drop it)'
                )
            )

    bundle_name = code.bundle_name if code is not None else None
    refs = code.refs if code is not None else []
    chain_json = code.chain if code is not None else None

    meta: dict[str, Any] = {
        "source": request.source,
        "archived_at": layout.now_iso(),
        "scope": request.scope,
        "bundle": bundle_name,
        "refs_count": len(refs),
        "release_count": len(releases),
        "with_assets": with_assets,
    }
    if snapshot:
        meta["source_mode"] = "snapshot"
    elif request.rewrite:
        meta["source_mode"] = "rewrite"
    if cutoff is not None:
        meta["cutoff"] = {
            "since": cutoff.since,
            "since_tag": cutoff.since_tag,
            "instant": cutoff.instant.isoformat(),
        }
    if request.rewrite and code is not None and code.rewritten is not None:
        meta["rewrite"] = {
            "commits": code.rewritten.commits,
            "refs": code.rewritten.ref_map(),
        }
    if not request.selection.is_default():
        meta["selection"] = request.selection.to_json()
    if not request.refs.is_default():
        meta["refs_rules"] = request.refs.to_json()
    if request.incremental and request.wants_code:
        meta["incremental"] = True
    if chain_json:
        meta["bundle_chain"] = chain_json
    if platform_name and request.wants_releases and host:
        meta["platform"] = platform_name
    if bundle_name is None and isinstance(old_meta.get("bundle"), str):
        warn(
            tr(
                'releases-only scope: "{bundle}" is left on disk but no longer '
                "referenced by meta.json",
                bundle=old_meta["bundle"],
            )
        )
    layout.write_meta(out_dir, meta)

    if bundle_name is not None and chain_json:
        chain.prune(out_dir, {str(item.get("path")) for item in chain_json})

    if request.tracker:
        _write_state(out_dir, request, meta, with_assets, platform_name)

    return ArchiveResult(
        archive_id=archive_id,
        out_dir=out_dir,
        scope=request.scope,
        snapshot=snapshot,
        with_assets=with_assets,
        bundle_path=code.bundle if code is not None else None,
        refs=refs,
        releases=releases,
        cutoff=cutoff,
        asset_failures=asset_failures,
        incremental=bool(meta.get("incremental")),
        rewritten=code.rewritten if code is not None else None,
        unchanged=bool(code is not None and code.unchanged),
        orphans=code.orphans if code is not None else 0,
    )


__all__ = [
    "SCOPES",
    "SCOPE_ALL",
    "SCOPE_CODE",
    "SCOPE_RELEASES",
    "ArchiveRequest",
    "ArchiveResult",
    "run_archive",
]
