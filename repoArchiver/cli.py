"""Command-line interface for repoArchiver."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import replace
from pathlib import Path

from . import __version__, chain, i18n, layout, progress, schedule, track, verify
from .archiver import (
    SCOPE_ALL,
    SCOPE_CODE,
    SCOPE_RELEASES,
    ArchiveRequest,
    run_archive,
)
from .errors import ArchiveError, RepoArchiverError, warn
from .http import HttpOptions
from .i18n import tr
from .refs import RefRules, visible_refs
from .releases import asset_headers, count_assets, read_index
from .restore import restore
from .selection import Selection

SHORT_COMMANDS = {
    "-a": "archive",
    "-l": "list",
    "-x": "restore",
    "-i": "inspect",
    "-s": "sync",
}

_EPILOG = """\
examples:
  repoArchiver archive https://github.com/psf/requests
  repoArchiver archive https://gitlab.com/inkscape/inkscape -t upstream
  repoArchiver archive git@github.com:psf/requests.git --with-assets -t nightly
  repoArchiver archive https://github.com/psf/requests --only-releases -t nightly
  repoArchiver archive https://github.com/psf/requests --only-code --snapshot
  repoArchiver archive https://github.com/psf/requests --only-code --incremental
  repoArchiver archive . --include-ref "refs/heads/*" --exclude-ref "refs/heads/wip/*"
  repoArchiver archive https://github.com/psf/requests --since 2024-01-01 --rewrite-history
  repoArchiver archive ./checkout --dir /data/archives/requests
  repoArchiver list psf__requests -r refs/tags/
  repoArchiver restore psf__requests v2.32.3 -d ./requests-2.32.3
  repoArchiver archives
  repoArchiver sync
  repoArchiver verify --all
  repoArchiver compact psf__requests
  repoArchiver schedule --interval 6h

The short flags -a / -l / -x / -i / -s are accepted in front of the subcommand name.
"""


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help=tr("config file (default: ~/.repoArchiver/config.toml)"),
    )


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--release",
        metavar="GLOB",
        action="append",
        default=None,
        help=tr("only download releases whose tag matches GLOB (repeatable)"),
    )
    parser.add_argument(
        "--skip-release",
        metavar="GLOB",
        action="append",
        default=None,
        help=tr("skip releases whose tag matches GLOB (repeatable)"),
    )
    parser.add_argument(
        "--asset",
        metavar="GLOB",
        action="append",
        default=None,
        help=tr("only download assets matching GLOB (repeatable)"),
    )
    parser.add_argument(
        "--skip-asset",
        metavar="GLOB",
        action="append",
        default=None,
        help=tr("skip assets matching GLOB (repeatable)"),
    )
    parser.add_argument(
        "--no-source",
        action="store_true",
        help=tr("do not save the auto-generated source zip/tar.gz"),
    )


def _add_refs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include-ref",
        metavar="GLOB",
        action="append",
        default=None,
        help=tr("only archive refs matching GLOB, e.g. refs/heads/main (repeatable)"),
    )
    parser.add_argument(
        "--exclude-ref",
        metavar="GLOB",
        action="append",
        default=None,
        help=tr("skip refs matching GLOB; their history is not stored (repeatable)"),
    )
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help=tr("pin commits no ref points at (dangling work) under refs/repoArchiver/orphans/"),
    )


def _add_globals(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--lang",
        metavar="LANG",
        default=None,
        help=tr(
            "interface language: {languages} (default: config, $REPOARCHIVER_LANG, or the locale)",
            languages=", ".join(i18n.LANGUAGES),
        ),
    )
    parser.add_argument(
        "--archives-dir",
        metavar="PATH",
        default=None,
        help=tr("directory holding the archives (default: config key archives_dir, i.e. ./archives)"),
    )
    parser.add_argument(
        "--progress",
        metavar="WHEN",
        choices=progress.MODES,
        default=None,
        help=tr(
            "progress output: auto (only on a terminal), always, never (default: {default})",
            default=progress.AUTO,
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=tr("same as --progress never"),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=tr("also forward the raw git output while working"),
    )
    trace_group = parser.add_mutually_exclusive_group()
    trace_group.add_argument(
        "--trace",
        dest="trace",
        action="store_true",
        default=None,
        help=tr("refresh tracked archives before running the command (default: config 'trace')"),
    )
    trace_group.add_argument(
        "--no-trace",
        dest="trace",
        action="store_false",
        default=None,
        help=tr("never refresh tracked archives before running the command"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repoArchiver",
        description=tr(
            "Archive a repository's code bundle, its releases (metadata + assets, including "
            "the generated source archives), or both; keep tracked archives up to date and "
            "restore refs or release assets from them."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_globals(parser)

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    archive = subparsers.add_parser(
        "archive",
        aliases=["a"],
        help=tr("archive a repository"),
        description=tr(
            "Store code.bundle and/or release metadata (plus assets). --only-releases skips "
            "git entirely, --only-code skips the release API. --since/--since-tag cut off old "
            "releases; on the code side they need --snapshot or --rewrite-history. "
            "--incremental appends deltas instead of rebuilding, and --include-ref/"
            "--exclude-ref prune refs for real."
        ),
    )
    archive.add_argument("url", help=tr("repository URL or local path"))
    archive.add_argument(
        "-t",
        "--tracker",
        metavar="NAME",
        help=tr(
            "tracker label; writing state.json marks the archive as tracked "
            "(then `sync`/`trace` keep it up to date)"
        ),
    )
    archive.add_argument(
        "--dir",
        metavar="DIR",
        help=tr(
            "store the archive in DIR instead of <archives-dir>/<owner>__<repo> "
            "(later commands accept that path as the archive id)"
        ),
    )
    scope_group = archive.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--only-releases",
        action="store_true",
        help=tr("store release metadata and assets only (no code.bundle, no clone)"),
    )
    scope_group.add_argument(
        "--only-code",
        action="store_true",
        help=tr("store code.bundle only (the release API is never queried)"),
    )
    assets_group = archive.add_mutually_exclusive_group()
    assets_group.add_argument(
        "--with-assets",
        action="store_true",
        help=tr("download release assets (default with --only-releases)"),
    )
    assets_group.add_argument(
        "--no-assets",
        action="store_true",
        help=tr("record release metadata without downloading assets"),
    )
    _add_selection(archive)
    cutoff_group = archive.add_mutually_exclusive_group()
    cutoff_group.add_argument(
        "--since",
        metavar="DATE",
        help=tr("only keep releases and snapshot refs at/after DATE (ISO 8601, e.g. 2024-01-01)"),
    )
    cutoff_group.add_argument(
        "--since-tag",
        metavar="TAG",
        help=tr("like --since, using TAG's release date (or commit date) as the cutoff"),
    )
    archive.add_argument(
        "--snapshot",
        action="store_true",
        help=tr("store code-only snapshots (one root commit per ref, no history)"),
    )
    archive.add_argument(
        "--rewrite-history",
        action="store_true",
        help=tr(
            "keep commits at/after the cutoff, rewritten so the bundle stands alone "
            "(hashes change; needs --since or --since-tag)"
        ),
    )
    archive.add_argument(
        "--incremental",
        action="store_true",
        help=tr("keep the first bundle as a base and append small deltas on refresh"),
    )
    archive.add_argument(
        "--platform",
        metavar="NAME",
        choices=("github", "gitlab", "gitea", "bitbucket"),
        help=tr("forge behind a self-hosted host (default: detected from the host name)"),
    )
    _add_refs(archive)
    _add_common(archive)
    archive.set_defaults(handler=_cmd_archive)

    syncing = subparsers.add_parser(
        "sync",
        aliases=["s"],
        help=tr("refresh tracked archives"),
        description=tr(
            "Re-fetch releases and assets (and code) for tracked archives. Only additions and "
            "missing files are downloaded; the result is logged to history.jsonl."
        ),
    )
    syncing.add_argument(
        "archive_ids",
        nargs="*",
        metavar="archive_id",
        help=tr("archives to sync (default: every tracked archive); an archive path works too"),
    )
    syncing.add_argument(
        "-t",
        "--tracker",
        metavar="NAME",
        help=tr("only sync archives carrying this tracker label"),
    )
    syncing.add_argument(
        "--dry-run",
        action="store_true",
        help=tr("report what would be added without downloading or writing"),
    )
    _add_common(syncing)
    syncing.set_defaults(handler=_cmd_sync)

    listing = subparsers.add_parser(
        "list",
        aliases=["l"],
        help=tr("list refs and releases of an archive"),
        description=tr(
            "Merge bundle refs and releases into one REF | HASH | TAG? | RELEASE? table."
        ),
    )
    listing.add_argument("archive_id", help=tr("archive id (e.g. psf__requests) or archive path"))
    listing.add_argument(
        "-r",
        "--ref",
        metavar="PREFIX",
        help=tr("only show refs starting with PREFIX (exact prefix, no fuzzy matching)"),
    )
    listing.add_argument(
        "--all-refs",
        action="store_true",
        help=tr("also show repoArchiver's internal refs (orphan pins)"),
    )
    _add_common(listing)
    listing.set_defaults(handler=_cmd_list)

    overview = subparsers.add_parser(
        "archives",
        aliases=["ls"],
        help=tr("list the archives on disk"),
        description=tr(
            "One row per archive under the archives directory: scope, refs, releases, size, "
            "tracker and last sync. Use `list` for the contents of one archive."
        ),
    )
    overview.add_argument("--json", action="store_true", help=tr("machine-readable output"))
    _add_common(overview)
    overview.set_defaults(handler=_cmd_archives)

    restoring = subparsers.add_parser(
        "restore",
        aliases=["x"],
        help=tr("restore a ref from an archive"),
        description=tr(
            "Check out a ref from code.bundle; release assets are copied with --with-assets. "
            "Releases-only archives restore their assets instead of code."
        ),
    )
    restoring.add_argument("archive_id", help=tr("archive id (e.g. psf__requests) or archive path"))
    restoring.add_argument("ref", help=tr("branch, tag, or full ref name"))
    restoring.add_argument(
        "-d",
        "--dest",
        metavar="DIR",
        help=tr("destination directory (default: ./<repo>)"),
    )
    restoring.add_argument("--force", action="store_true", help=tr("overwrite a non-empty destination"))
    restoring.add_argument(
        "--strict",
        action="store_true",
        help=tr("fail instead of falling back when a tag is missing from the bundle"),
    )
    restoring.add_argument("--with-assets", action="store_true", help=tr("copy/download release assets"))
    restoring.add_argument(
        "--fetch-missing",
        action="store_true",
        help=tr("also download assets this archive never stored (default: only restore archived ones)"),
    )
    restoring.add_argument(
        "--assets-only",
        action="store_true",
        help=tr("restore the release assets of REF only, never the code"),
    )
    _add_common(restoring)
    restoring.set_defaults(handler=_cmd_restore)

    inspecting = subparsers.add_parser(
        "inspect",
        aliases=["i"],
        help=tr("show archive metadata"),
        description=tr("Print a summary of meta.json, state.json, releases/index.json and history."),
    )
    inspecting.add_argument("archive_id", help=tr("archive id (e.g. psf__requests) or archive path"))
    _add_common(inspecting)
    inspecting.set_defaults(handler=_cmd_inspect)

    verifying = subparsers.add_parser(
        "verify",
        aliases=["check"],
        help=tr("check archives for inconsistencies"),
        description=tr(
            "Compare meta.json, the bundle chain, release metadata and the files on disk; "
            "exits 1 when an error is found."
        ),
    )
    verifying.add_argument(
        "archive_ids",
        nargs="*",
        metavar="archive_id",
        help=tr("archives to check (default: all of them); an archive path works too"),
    )
    verifying.add_argument(
        "--all",
        action="store_true",
        help=tr("check every archive under the archives directory (the default)"),
    )
    verifying.add_argument(
        "--strict",
        action="store_true",
        help=tr("treat warnings as errors too"),
    )
    verifying.add_argument("--json", action="store_true", help=tr("machine-readable output"))
    _add_common(verifying)
    verifying.set_defaults(handler=_cmd_verify)

    compacting = subparsers.add_parser(
        "compact",
        help=tr("fold an incremental bundle chain into one bundle"),
        description=tr(
            "Replay the chain and write a single self-contained code.bundle, then remove the "
            "delta files."
        ),
    )
    compacting.add_argument("archive_id", help=tr("archive id (e.g. psf__requests) or archive path"))
    _add_common(compacting)
    compacting.set_defaults(handler=_cmd_compact)

    scheduling = subparsers.add_parser(
        "schedule",
        help=tr("print scheduler entries for periodic syncs"),
        description=tr(
            "Print cron / schtasks / systemd snippets that run `sync` on an interval. "
            "Nothing is installed for you."
        ),
    )
    scheduling.add_argument(
        "--interval",
        metavar="DURATION",
        default="6h",
        help=tr("how often to sync, e.g. 30m, 6h, 1d (default: 6h)"),
    )
    scheduling.add_argument(
        "archive_ids",
        nargs="*",
        metavar="archive_id",
        help=tr("sync only these archives (default: every tracked archive)"),
    )
    scheduling.add_argument("--json", action="store_true", help=tr("machine-readable output"))
    _add_common(scheduling)
    scheduling.set_defaults(handler=_cmd_schedule)

    return parser


def _format_size(size: int) -> str:
    return progress.format_size(size)


def _archive_path(config: layout.Config, archive_ref: str) -> Path:
    """Resolve an archive id or an explicit archive directory."""

    candidate = Path(archive_ref).expanduser()
    if candidate.is_dir() and (candidate / "meta.json").is_file():
        return candidate.resolve()
    directory = layout.archive_dir(config.archives_dir, archive_ref)
    if not directory.is_dir():
        raise ArchiveError(
            tr(
                'archive "{archive_id}" not found under "{archives_dir}" '
                "(run `repoArchiver archive ...` first)",
                archive_id=archive_ref,
                archives_dir=config.archives_dir,
            )
        )
    return directory


def _verify_target(archive_ref: str) -> str | Path:
    """Return the directory of archive_ref if it is a path, else the id."""

    candidate = Path(archive_ref).expanduser()
    if candidate.is_dir() and (candidate / "meta.json").is_file():
        return candidate.resolve()
    return archive_ref


def _cmd_archive(args: argparse.Namespace, config: layout.Config) -> int:
    if args.only_releases:
        scope = SCOPE_RELEASES
    elif args.only_code:
        scope = SCOPE_CODE
    else:
        scope = SCOPE_ALL

    selection = Selection(
        releases=tuple(args.release or ()),
        skip_releases=tuple(args.skip_release or ()),
        assets=tuple(args.asset or ()),
        skip_assets=tuple(args.skip_asset or ()),
        source_archives=not args.no_source,
    )
    with_assets = True if args.with_assets else False if args.no_assets else None

    request = ArchiveRequest(
        source=args.url,
        scope=scope,
        snapshot=args.snapshot,
        with_assets=with_assets,
        selection=selection,
        since=args.since,
        since_tag=args.since_tag,
        tracker=args.tracker,
        platform=args.platform,
        refs=RefRules(
            include=tuple(args.include_ref or ()),
            exclude=tuple(args.exclude_ref or ()),
            keep_orphans=args.keep_orphans,
        ),
        incremental=args.incremental,
        rewrite=args.rewrite_history,
        out_dir=args.dir,
    )
    result = run_archive(request, config)

    print(tr('archived "{archive_id}" -> "{out_dir}"', archive_id=result.archive_id, out_dir=result.out_dir))
    details: list[str] = []
    if scope == SCOPE_RELEASES:
        details.append(tr("releases only"))
    elif scope == SCOPE_CODE:
        details.append(tr("code only"))
    if result.snapshot:
        details.append(tr("snapshot"))
    if result.rewritten is not None:
        details.append(tr("rewrite: {detail}", detail=result.rewritten.describe()))
    if result.incremental:
        details.append(tr("incremental"))
    if result.orphans:
        details.append(tr("orphans: {count}", count=result.orphans))
    if result.cutoff is not None:
        details.append(tr("cutoff {label}", label=result.cutoff.label))

    summary = [
        tr("refs: {count}", count=len(result.refs)),
        tr("releases: {count}", count=len(result.releases)),
    ]
    if result.bundle_path is not None and result.bundle_path.is_file():
        summary.append(tr("bundle: {size}", size=_format_size(result.bundle_path.stat().st_size)))
    if result.with_assets:
        summary.append(tr("assets: {count}", count=result.assets_saved))
    suffix = f"  ({', '.join(details)})" if details else ""
    print("  " + "  ".join(summary) + suffix)
    if not selection.is_default():
        print("  " + tr("selection: {detail}", detail=selection.describe()))
    if not request.refs.is_default():
        print("  " + tr("refs rules: {detail}", detail=request.refs.describe()))
    return 0


def _cmd_sync(args: argparse.Namespace, config: layout.Config) -> int:
    if args.archive_ids and args.tracker:
        raise ArchiveError(
            "-t/--tracker filters the archive list; drop it when naming archives explicitly"
        )

    targets: list[tuple[str, Path]] = []
    if args.archive_ids:
        for archive_ref in args.archive_ids:
            targets.append((archive_ref, _archive_path(config, archive_ref)))
    else:
        targets = track.tracked_archives(config, tracker=args.tracker)

    if not targets:
        warn(tr("no tracked archives found (run `repoArchiver archive -t NAME <url>` first)"))
        return 0

    failures = 0
    for archive_id, directory in targets:
        label = directory.name if Path(archive_id).is_absolute() or path_like(archive_id) else archive_id
        try:
            outcome = track.sync_archive(directory, config, dry_run=args.dry_run)
        except (RepoArchiverError, OSError) as exc:
            warn(tr('sync of "{archive_id}" failed: {detail}', archive_id=label, detail=exc))
            failures += 1
            continue
        message = tr('synced "{archive_id}": {detail}', archive_id=label, detail=outcome.describe())
        if args.dry_run:
            message = tr('would sync "{archive_id}": {detail}', archive_id=label, detail=outcome.describe())
        print(message)
    return 1 if failures else 0


def path_like(value: str) -> bool:
    """Whether value looks like a path rather than a plain archive id."""

    return any(sep in value for sep in ("/", "\\")) or value.startswith(".")


def _display_width(text: str) -> int:
    """Terminal width of text (CJK characters occupy two cells)."""

    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """Left-align text in a column width display cells wide."""

    return text + " " * max(0, width - _display_width(text))


def _cmd_archives(args: argparse.Namespace, config: layout.Config) -> int:
    root = Path(config.archives_dir)
    rows: list[dict[str, object]] = []
    if root.is_dir():
        for candidate in sorted(root.iterdir()):
            if not candidate.is_dir() or candidate.name.startswith("."):
                continue
            try:
                meta = layout.read_meta(candidate)
            except RepoArchiverError:
                continue
            state = layout.read_state(candidate) or {}
            entries = chain.chain_entries(candidate, meta)
            size = sum(entry.path.stat().st_size for entry in entries if entry.path.is_file())
            rows.append(
                {
                    "id": candidate.name,
                    "path": str(candidate),
                    "scope": meta.get("scope", "all"),
                    "refs": meta.get("refs_count", 0),
                    "releases": meta.get("release_count", 0),
                    "size": size,
                    "tracker": state.get("tracker") or "-",
                    "last_sync": state.get("last_sync_at") or "-",
                }
            )

    if args.json:
        print(json.dumps({"archives_dir": str(root), "archives": rows}, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        warn(tr('no archives found under "{archives_dir}"', archives_dir=root))
        return 0

    headers = (
        tr("ID"),
        tr("SCOPE"),
        tr("REFS"),
        tr("RELEASES"),
        tr("SIZE"),
        tr("TRACKER"),
        tr("LAST SYNC"),
    )
    table = [
        (
            str(row["id"]),
            str(row["scope"]),
            str(row["refs"]),
            str(row["releases"]),
            _format_size(int(row["size"])),
            str(row["tracker"]),
            str(row["last_sync"]),
        )
        for row in rows
    ]
    widths = [_display_width(head) for head in headers]
    for line in table:
        widths = [
            max(width, _display_width(cell))
            for width, cell in zip(widths, line, strict=True)
        ]
    print("  ".join(_pad(head, width) for head, width in zip(headers, widths, strict=True)))
    print("  ".join("-" * width for width in widths))
    for line in table:
        print("  ".join(_pad(cell, width) for cell, width in zip(line, widths, strict=True)))
    print()
    print(tr("{count} archive(s) under {path}", count=len(rows), path=root))
    return 0


def _cmd_list(args: argparse.Namespace, config: layout.Config) -> int:
    directory = _archive_path(config, args.archive_id)
    meta = layout.read_meta(directory)
    entries = chain.chain_entries(directory, meta)
    missing = [entry.name for entry in entries if not entry.path.is_file()]
    refs = chain.chain_heads([entry for entry in entries if entry.path.is_file()])
    if not args.all_refs:
        refs = visible_refs(refs)
    releases = read_index(directory / "releases" / "index.json")

    prefix = args.ref
    by_tag: dict[str, dict[str, object]] = {}
    for release in releases:
        tag = release.get("tag_name")
        if isinstance(tag, str) and tag:
            by_tag[tag] = release

    rows: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for commit, ref in refs:
        if prefix and not ref.startswith(prefix):
            continue
        tag = ref[len("refs/tags/") :] if ref.startswith("refs/tags/") else ""
        release = by_tag.get(tag) if tag else None
        if release is not None:
            seen.add(tag)
        rows.append((ref, commit[:12], tag or "-", (release or {}).get("tag_name", "-") or "-"))

    for tag, release in sorted(by_tag.items()):
        if tag in seen:
            continue
        if prefix and not (tag.startswith(prefix) or f"refs/tags/{tag}".startswith(prefix)):
            continue
        commit = release.get("commit_hash")
        short = commit[:12] if isinstance(commit, str) and commit else "-"
        rows.append(("-", short, "-", tag))

    print(
        tr(
            "archive: {archive_id}  source: {source}",
            archive_id=args.archive_id,
            source=meta.get("source", "?"),
        )
    )
    print(
        tr(
            "refs: {refs}  releases: {releases}",
            refs=len(refs),
            releases=len(releases),
        )
    )
    if not entries:
        print(tr("code: none (releases-only archive)"))
    elif len(entries) > 1:
        print(tr("code: {count} bundles (incremental chain)", count=len(entries)))
    if missing:
        warn(tr("missing bundle file(s): {names}", names=", ".join(missing)))
    print()
    if not rows:
        print(tr("(no matching refs)"))
        return 0

    ref_width = max(len("REF"), max((len(row[0]) for row in rows), default=0))
    hash_width = max(len("HASH"), max((len(row[1]) for row in rows), default=0))
    tag_width = max(len("TAG?"), max((len(row[2]) for row in rows), default=0))
    release_width = max(len("RELEASE?"), max((len(row[3]) for row in rows), default=0))
    print(f"{'REF':<{ref_width}}  {'HASH':<{hash_width}}  {'TAG?':<{tag_width}}  {'RELEASE?':<{release_width}}")
    print(f"{'-' * ref_width}  {'-' * hash_width}  {'-' * tag_width}  {'-' * release_width}")
    for ref, commit, tag, release_tag in rows:
        print(f"{ref:<{ref_width}}  {commit:<{hash_width}}  {tag:<{tag_width}}  {release_tag:<{release_width}}")
    return 0


def _cmd_restore(args: argparse.Namespace, config: layout.Config) -> int:
    directory = _archive_path(config, args.archive_id)
    meta = layout.read_meta(directory)
    dest = Path(args.dest).expanduser() if args.dest else Path.cwd() / layout.repo_name(args.archive_id)

    source = meta.get("source")
    headers: dict[str, str] | None = None
    token: str | None = None
    if isinstance(source, str) and source:
        host, _owner, _repo = layout.parse_source(source)
        token = config.token_for(host)
        platform_name = meta.get("platform") if isinstance(meta.get("platform"), str) else None
        headers = asset_headers(source, token=token, platform=platform_name)

    restore(
        directory,
        args.ref,
        dest,
        force=args.force,
        strict=args.strict,
        with_assets=args.with_assets,
        assets_only=args.assets_only,
        fetch_missing=args.fetch_missing,
        token=token,
        headers=headers,
        options=HttpOptions.from_config(config),
    )
    print(
        tr(
            'restored "{ref}" from archive "{archive_id}" into "{dest}"',
            ref=args.ref,
            archive_id=args.archive_id,
            dest=dest,
        )
    )
    return 0


def _cmd_inspect(args: argparse.Namespace, config: layout.Config) -> int:
    directory = _archive_path(config, args.archive_id)
    meta = layout.read_meta(directory)
    state = layout.read_state(directory)
    releases = read_index(directory / "releases" / "index.json")
    bundle_path = layout.bundle_path(directory, meta)
    counts = count_assets(releases)

    print(tr("archive: {archive_id}", archive_id=args.archive_id))
    print(tr("path: {path}", path=directory))
    print(tr("source: {source}", source=meta.get("source", "?")))
    print(tr("archived_at: {when}", when=meta.get("archived_at", "?")))
    print(tr("scope: {scope}", scope=meta.get("scope", "all")))
    entries = chain.chain_entries(directory, meta)
    if bundle_path is None:
        print(tr("bundle: none (releases-only archive)"))
    elif bundle_path.is_file():
        print(
            tr(
                "bundle: {name} ({size})",
                name=bundle_path.name,
                size=_format_size(bundle_path.stat().st_size),
            )
        )
    else:
        print(tr("bundle: {name} (missing)", name=bundle_path.name))
    if len(entries) > 1:
        print(tr("chain: {count} bundle(s), incremental", count=len(entries)))
    platform_name = meta.get("platform")
    if isinstance(platform_name, str):
        print(tr("platform: {name}", name=platform_name))
    print(tr("refs: {count}", count=meta.get("refs_count", "?")))
    print(tr("releases: {count}", count=meta.get("release_count", "?")))
    print(
        tr(
            "assets: {saved}/{total} saved ({source} source)",
            saved=counts["saved"],
            total=counts["total"],
            source=counts["source"],
        )
    )
    if meta.get("incremental"):
        print(tr("incremental: yes"))
    if meta.get("source_mode"):
        print(tr("source mode: {mode}", mode=meta["source_mode"]))
    rewrite = meta.get("rewrite")
    if isinstance(rewrite, dict):
        print(tr("rewrite: {commits} commit(s) kept", commits=rewrite.get("commits", "?")))
    cutoff = meta.get("cutoff")
    if isinstance(cutoff, dict):
        source = cutoff.get("since") or cutoff.get("since_tag")
        print(tr("cutoff: {label} ({instant})", label=source, instant=cutoff.get("instant", "?")))
    refs_rules = meta.get("refs_rules")
    if isinstance(refs_rules, dict):
        print(tr("refs rules: {detail}", detail=RefRules.from_json(refs_rules).describe()))
    selection_json = meta.get("selection")
    if isinstance(selection_json, dict):
        print(tr("selection: {detail}", detail=Selection.from_json(selection_json).describe()))

    if state:
        print()
        print(tr("tracker state:"))
        for key in ("tracker", "tracked_at", "last_sync_at", "refs_count", "release_count"):
            if key in state:
                print(f"  {key}: {state[key]}")
        state_selection = state.get("selection")
        if isinstance(state_selection, dict):
            print(f"  selection: {Selection.from_json(state_selection).describe()}")
        if state.get("incremental"):
            print("  " + tr("incremental: yes"))
        if state.get("platform"):
            print(f"  platform: {state['platform']}")
    else:
        print(tr("tracker state: none"))

    if releases:
        print()
        print(tr("releases:"))
        for release in releases:
            tag = release.get("tag_name", "?")
            name = release.get("name") or ""
            published = release.get("published_at") or "-"
            assets = len(release.get("assets") or [])
            flags = []
            if release.get("prerelease"):
                flags.append(tr("prerelease"))
            if release.get("draft"):
                flags.append(tr("draft"))
            suffix = f" [{', '.join(flags)}]" if flags else ""
            title = f" ({name})" if name and name != tag else ""
            print(f"  {tag:<20} {published}  assets={assets}{title}{suffix}")

    history = layout.read_history(directory, limit=5)
    if history:
        print()
        print(tr("history:"))
        for entry in history:
            at = entry.get("at", "?")
            event = str(entry.get("event", "?"))
            bits: list[str] = []
            for key, label in (
                ("added_releases", tr("release")),
                ("added_assets", tr("asset")),
                ("added_refs", tr("ref")),
            ):
                values = entry.get(key)
                if isinstance(values, list) and values:
                    bits.append(tr("+{count} {kind}(s)", count=len(values), kind=label))
            failed = entry.get("failed_assets")
            if isinstance(failed, list) and failed:
                bits.append(tr("{count} failed", count=len(failed)))
            print(f"  {at}  {event:<8} {', '.join(bits) or tr('no changes')}")
    return 0


def _cmd_verify(args: argparse.Namespace, config: layout.Config) -> int:
    targets = args.archive_ids or None
    # A missing archive is a finding for verify, not a usage error.
    resolved = [_verify_target(item) for item in targets] if targets else None
    reports = verify.verify_all(config.archives_dir, resolved)
    failed = [report for report in reports if report.errors]
    if args.strict:
        failed = [
            report
            for report in reports
            if any(problem.level in (verify.ERROR, verify.WARNING) for problem in report.problems)
        ]

    if args.json:
        print(
            json.dumps(
                {
                    "archives": [report.to_json() for report in reports],
                    "ok": not failed,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1 if failed else 0

    if not reports:
        warn(tr('no archives found under "{archives_dir}"', archives_dir=config.archives_dir))
        return 0

    any_warning = False
    for report in reports:
        status = tr("ok") if report.ok else tr("FAILED")
        print(tr("{archive_id}: {status}  ({path})", archive_id=report.archive_id, status=status, path=report.path))
        for problem in report.problems:
            print(f"  {problem.level.upper():<7} {problem.code}: {problem.message}")
            if problem.level == verify.WARNING:
                any_warning = True
    print()
    print(verify.summarize(reports))
    if any_warning and not failed:
        print(tr("(warnings do not fail verify; use --strict to change that)"))
    return 1 if failed else 0


def _cmd_compact(args: argparse.Namespace, config: layout.Config) -> int:
    directory = _archive_path(config, args.archive_id)
    meta = layout.read_meta(directory)
    before = chain.chain_entries(directory, meta)
    if len(before) <= 1:
        print(tr('"{archive_id}" is already a single bundle; nothing to compact', archive_id=args.archive_id))
        return 0
    updated = chain.compact(directory, meta)
    layout.write_meta(directory, updated)
    base = layout.bundle_path(directory, updated)
    size = _format_size(base.stat().st_size) if base is not None and base.is_file() else "?"
    print(
        tr(
            'compacted "{archive_id}": {count} bundle(s) -> code.bundle ({size})',
            archive_id=args.archive_id,
            count=len(before),
            size=size,
        )
    )
    return 0


def _cmd_schedule(args: argparse.Namespace, config: layout.Config) -> int:
    interval = layout.parse_duration(args.interval, key="--interval")
    if interval <= 0:
        raise ArchiveError("--interval must be greater than zero")
    plan = schedule.plan(
        interval,
        archives_dir=config.archives_dir,
        archive_ids=tuple(args.archive_ids),
    )
    if args.json:
        print(json.dumps(plan.to_json(), indent=2, ensure_ascii=False))
        return 0

    print(f"# sync every {args.interval}  ({int(interval)}s)")
    print(tr("# Nothing is installed automatically; copy the line you need."))
    print()
    print(tr("cron (Linux / macOS):"))
    print(f"  {plan.cron}")
    print()
    print(tr("Windows (schtasks, run once in cmd.exe):"))
    print(f"  {plan.schtasks}")
    print()
    print(tr("systemd timer (OnUnitActiveSec line of repoarchiver.timer):"))
    print(f"  {plan.systemd}")
    print()
    print(
        tr(
            "tip: set trace_interval in the config to throttle the on-launch trace "
            '(e.g. trace_interval = "6h") instead of relying on a scheduler.'
        )
    )
    return 0


def _trace_before(args: argparse.Namespace, config: layout.Config) -> None:
    """Refresh the tracked archive a read-only command is about to touch."""

    if args.command not in {"list", "inspect", "restore"}:
        return
    archive_id = getattr(args, "archive_id", None)
    if not isinstance(archive_id, str) or not archive_id:
        return
    try:
        directory = _archive_path(config, archive_id)
    except RepoArchiverError:
        return
    state = None
    try:
        state = layout.read_state(directory)
    except RepoArchiverError:
        state = None
    if state is None:
        return
    if not schedule.should_trace(state, config.trace_interval):
        return
    outcome = track.trace_archive(directory, config)
    if outcome is not None:
        print(tr("trace {archive_id}: {detail}", archive_id=outcome.archive_id, detail=outcome.describe()))


def _prescan(argv: list[str]) -> argparse.Namespace:
    """Read the options that must apply before help text is built."""

    prescan = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    prescan.add_argument("-c", "--config", default=None)
    prescan.add_argument("--lang", default=None)
    prescan.add_argument("--archives-dir", default=None)
    prescan.add_argument("--progress", default=None)
    prescan.add_argument("-q", "--quiet", action="store_true")
    prescan.add_argument("--verbose", action="store_true")
    known, _rest = prescan.parse_known_args(argv)
    return known


def _apply_overrides(config: layout.Config, archives_dir: str | None) -> layout.Config:
    if not archives_dir:
        return config
    path = Path(archives_dir).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return replace(config, archives_dir=path)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SHORT_COMMANDS:
        argv[0] = SHORT_COMMANDS[argv[0]]

    prescan = _prescan(argv)
    try:
        config = _apply_overrides(layout.load_config(prescan.config), prescan.archives_dir)
        i18n.set_language(
            i18n.resolve_language(flag=prescan.lang, config=config.language)
        )
        parser = build_parser()
        args = parser.parse_args(argv)
        if not getattr(args, "command", None):
            parser.print_help()
            return 2

        mode = args.progress or config.progress or progress.AUTO
        if args.quiet:
            mode = progress.NEVER
        progress.configure(mode, verbose=bool(args.verbose))

        tracing = config.trace if args.trace is None else bool(args.trace)
        if tracing:
            _trace_before(args, config)
        return int(args.handler(args, config))
    except RepoArchiverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(tr("interrupted"), file=sys.stderr)
        return 130
    finally:
        progress.reset()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
