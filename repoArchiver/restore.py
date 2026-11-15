"""Restore an archived ref into a working directory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import chain, layout
from .bundle import git
from .errors import (
    AmbiguousRef,
    ArchiveError,
    DestNotEmpty,
    GitError,
    RefNotFound,
    StrictFallback,
    warn,
)
from .i18n import tr
from .releases import download_asset, find_release, read_index

_HEX = "0123456789abcdef"


def resolve_ref(input_ref: str, refs: list[tuple[str, str]]) -> str:
    """Resolve input_ref to a full ref name."""

    name = (input_ref or "").strip()
    if not name:
        raise RefNotFound(input_ref)

    names = [ref for _commit, ref in refs]
    if name in names:
        return name

    suffixed = sorted(ref for ref in names if ref.endswith("/" + name))
    if len(suffixed) == 1:
        return suffixed[0]
    if len(suffixed) > 1:
        raise AmbiguousRef(name, suffixed)

    leaves = sorted(ref for ref in names if ref.rsplit("/", 1)[-1] == name)
    if len(leaves) == 1:
        return leaves[0]
    if len(leaves) > 1:
        raise AmbiguousRef(name, leaves)
    raise RefNotFound(name)


def match_commit_prefix(refs: list[tuple[str, str]], value: str) -> str | None:
    """Resolve a possibly abbreviated commit hash to a unique commit."""

    text = (value or "").strip().lower()
    if len(text) < 7 or any(char not in _HEX for char in text):
        return None
    matches: set[str] = set()
    for commit, _ref in refs:
        if commit.startswith(text):
            matches.add(commit)
    return next(iter(matches)) if len(matches) == 1 else None


def _prepare_dest(dest: str | Path, force: bool) -> Path:
    target = Path(dest).expanduser()
    resolved = target.resolve()
    if resolved.parent == resolved:
        raise ArchiveError(tr("refusing to use a filesystem root as the restore destination"))
    if resolved == Path.home().resolve():
        raise ArchiveError(tr("refusing to use the home directory as the restore destination"))
    if resolved == Path.cwd().resolve():
        raise ArchiveError(
            tr("refusing to restore into the current working directory; pass --dest")
        )

    if target.exists():
        if not target.is_dir():
            raise ArchiveError(
                tr('destination "{target}" exists and is not a directory', target=target)
            )
        if any(target.iterdir()):
            if not force:
                raise DestNotEmpty(
                    tr(
                        'destination "{target}" is not empty (use --force to overwrite)',
                        target=target,
                    )
                )
            shutil.rmtree(target)
        else:
            target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _peel(dest: Path, revision: str) -> str:
    proc = git(
        ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        cwd=dest,
        check=False,
    )
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else revision


def _checkout(dest: Path, ref: str, commit: str) -> None:
    target_commit = _peel(dest, commit)
    if ref.startswith("refs/heads/"):
        branch = ref[len("refs/heads/") :]
        git(["checkout", "--quiet", "-B", branch, target_commit], cwd=dest)
    elif ref.startswith("refs/tags/"):
        tag = ref[len("refs/tags/") :]
        existing = git(["rev-parse", "--verify", "--quiet", ref], cwd=dest, check=False)
        if existing.returncode == 0 and _peel(dest, ref) == target_commit:
            git(["checkout", "--quiet", "--detach", target_commit], cwd=dest)
        else:
            git(["tag", "-f", tag, target_commit], cwd=dest)
            git(["checkout", "--quiet", "--detach", target_commit], cwd=dest)
    else:
        git(["checkout", "--quiet", "--detach", target_commit], cwd=dest)


def restore_code(
    bundle: str | Path,
    ref: str,
    dest: str | Path,
    force: bool = False,
) -> None:
    """Clone a single bundle into dest and check out ref."""

    restore_chain_code([chain.BundleEntry(Path(bundle), "base")], ref, dest, force)


def restore_chain_code(
    entries: list[chain.BundleEntry],
    ref: str,
    dest: str | Path,
    force: bool = False,
) -> None:
    """Replay a possibly incremental bundle chain and check out ref."""

    refs = chain.chain_heads(entries)
    commit = next((value for value, name in refs if name == ref), None)
    if commit is None:
        raise RefNotFound(ref)

    target = _prepare_dest(dest, force)
    try:
        chain.materialize(entries, target)
        _checkout(target, ref, commit)
    except GitError:
        shutil.rmtree(target, ignore_errors=True)
        raise


def restore_commit(
    entries: list[chain.BundleEntry],
    commit: str,
    dest: str | Path,
    force: bool = False,
    ref: str | None = None,
) -> None:
    """Replay a chain and check out one commit (detached)."""

    target = _prepare_dest(dest, force)
    try:
        chain.materialize(entries, target)
        git(["checkout", "--quiet", "--detach", _peel(target, commit)], cwd=target)
    except GitError:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _asset_local_path(asset: dict[str, Any], archive_dir: Path) -> Path | None:
    local = asset.get("local_path")
    if not isinstance(local, str) or not local:
        return None
    path = Path(local)
    if not path.is_absolute():
        path = archive_dir / path
    return path if path.is_file() else None


def _copy_assets(
    release: dict[str, Any],
    archive_dir: Path,
    dest: Path,
    token: str | None,
    fetch_missing: bool = False,
    *,
    headers: dict[str, str] | None = None,
    options: Any = None,
) -> int:
    """Copy the release's archived assets into dest/assets."""

    assets = release.get("assets") or []
    if not assets:
        return 0

    assets_dir = dest / "assets"
    copied = 0
    skipped = 0
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = Path(str(asset.get("name") or "")).name
        if not name:
            warn(
                tr(
                    "failed to download asset {name}: invalid asset name",
                    name=repr(asset.get("name")),
                )
            )
            continue
        target = assets_dir / name
        source = _asset_local_path(asset, archive_dir)
        if source is not None:
            assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
            continue

        if not fetch_missing:
            skipped += 1
            continue

        url = asset.get("url")
        if not isinstance(url, str) or not url:
            warn(
                tr(
                    "failed to download asset {name}: no local copy and no download URL",
                    name=name,
                )
            )
            skipped += 1
            continue
        size = asset.get("size") if isinstance(asset.get("size"), int) else None
        try:
            download_asset(
                url,
                target,
                token=token,
                expected_size=size,
                headers=headers,
                options=options,
            )
        except Exception as exc:  # noqa: BLE001 - a failed asset must not abort the restore
            warn(tr("failed to download asset {name}: {detail}", name=name, detail=exc))
            skipped += 1
            continue
        copied += 1

    if skipped:
        hint = "" if fetch_missing else tr(" (use --fetch-missing to download them)")
        warn(
            tr(
                "skipped {count} asset(s) not stored in this archive{hint}",
                count=skipped,
                hint=hint,
            )
        )
    return copied


def _restore_degraded(
    entries: list[chain.BundleEntry],
    archive_dir: Path,
    release: dict[str, Any],
    ref: str,
    dest: str | Path,
    force: bool,
    strict: bool,
    with_assets: bool,
    token: str | None,
    fetch_missing: bool = False,
    *,
    headers: dict[str, str] | None = None,
    options: Any = None,
) -> None:
    tag = str(release.get("tag_name") or ref)
    commit = release.get("commit_hash") if isinstance(release.get("commit_hash"), str) else None

    if strict:
        raise StrictFallback(
            tr('tag "{tag}" is not in the bundle; --strict forbids falling back', tag=tag)
        )
    if commit:
        warn(tr("tag {tag} not in bundle, falling back to commit {commit}", tag=tag, commit=commit))
    else:
        warn(
            tr(
                "tag {tag} not in bundle and the release records no commit hash, "
                "falling back to the default branch",
                tag=tag,
            )
        )

    target = _prepare_dest(dest, force)
    code_ok = False
    try:
        chain.materialize(entries, target)
        if commit:
            proc = git(
                ["checkout", "--quiet", "--detach", _peel(target, commit)],
                cwd=target,
                check=False,
            )
            if proc.returncode == 0:
                code_ok = True
            else:
                warn(
                    tr(
                        "commit {commit} is not in the bundle, "
                        "falling back to the default branch",
                        commit=commit,
                    )
                )
        if not code_ok:
            head = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target, check=False)
            branch = head.stdout.strip()
            if head.returncode != 0 or branch in {"", "HEAD"}:
                raise GitError(tr("the bundle has no usable default branch"))
            warn(tr('restoring the default branch "{branch}" instead', branch=branch))
            code_ok = True
    except GitError as exc:
        warn(tr("failed to restore code: {detail}", detail=exc))
        target.mkdir(parents=True, exist_ok=True)

    copied = (
        _copy_assets(
            release,
            archive_dir,
            target,
            token,
            fetch_missing,
            headers=headers,
            options=options,
        )
        if with_assets
        else 0
    )
    if not code_ok and copied == 0:
        raise ArchiveError(
            tr('neither code nor assets could be restored for tag "{tag}"', tag=tag)
        )
    if not code_ok:
        warn(tr("assets only: no code was restored"))


def _restore_assets_only(
    archive: Path,
    release: dict[str, Any] | None,
    ref: str,
    dest: str | Path,
    force: bool,
    token: str | None,
    *,
    code_less: bool,
    fetch_missing: bool = False,
    headers: dict[str, str] | None = None,
    options: Any = None,
) -> None:
    """Restore one release's assets, without any code checkout."""

    if release is None:
        if code_less:
            raise ArchiveError(
                tr(
                    'archive "{archive_id}" stores no code, and "{ref}" matches no release',
                    archive_id=archive.name,
                    ref=ref,
                )
            )
        raise RefNotFound(ref, archive_id=archive.name)

    tag = str(release.get("tag_name") or ref)
    if code_less:
        warn(
            tr(
                'archive "{archive_id}" stores no code; restoring the assets of {tag} only',
                archive_id=archive.name,
                tag=tag,
            )
        )

    target = _prepare_dest(dest, force)
    copied = _copy_assets(
        release, archive, target, token, fetch_missing, headers=headers, options=options
    )
    if copied == 0:
        raise ArchiveError(tr('no assets could be restored for tag "{tag}"', tag=tag))


def restore(
    dir_path: str | Path,
    ref: str,
    dest: str | Path,
    force: bool = False,
    strict: bool = False,
    with_assets: bool = False,
    assets_only: bool = False,
    fetch_missing: bool = False,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    options: Any = None,
) -> None:
    """Restore ref from the archive at dir_path into dest."""

    archive = Path(dir_path)
    if not archive.is_dir():
        raise ArchiveError(tr('archive not found: "{path}"', path=archive))

    meta = layout.read_meta(archive)
    entries = chain.chain_entries(archive, meta)
    missing = [entry for entry in entries if not entry.path.is_file()]
    if missing:
        raise ArchiveError(tr('bundle not found: "{path}"', path=missing[0].path))

    releases = read_index(archive / "releases" / "index.json")
    release = find_release(releases, ref)

    if assets_only:
        _restore_assets_only(
            archive,
            release,
            ref,
            dest,
            force,
            token,
            code_less=False,
            fetch_missing=fetch_missing,
            headers=headers,
            options=options,
        )
        return

    if not entries:
        _restore_assets_only(
            archive,
            release,
            ref,
            dest,
            force,
            token,
            code_less=True,
            fetch_missing=fetch_missing,
            headers=headers,
            options=options,
        )
        return

    refs = chain.chain_heads(entries)

    try:
        full_ref = resolve_ref(ref, refs)
    except AmbiguousRef:
        raise
    except RefNotFound as exc:
        commit = match_commit_prefix(refs, ref)
        if commit is not None:
            restore_commit(entries, commit, dest, force=force)
            if with_assets:
                if release is None:
                    warn(tr('no release matches "{ref}"; assets skipped', ref=ref))
                else:
                    _copy_assets(
                        release,
                        archive,
                        Path(dest).expanduser(),
                        token,
                        fetch_missing,
                        headers=headers,
                        options=options,
                    )
            return
        if release is None:
            raise RefNotFound(ref, archive_id=archive.name) from exc
        _restore_degraded(
            entries,
            archive,
            release,
            ref,
            dest,
            force=force,
            strict=strict,
            with_assets=with_assets,
            token=token,
            fetch_missing=fetch_missing,
            headers=headers,
            options=options,
        )
        return

    restore_chain_code(entries, full_ref, dest, force=force)
    if with_assets:
        if release is None:
            warn(tr('no release matches "{ref}"; assets skipped', ref=ref))
        else:
            _copy_assets(
                release,
                archive,
                Path(dest).expanduser(),
                token,
                fetch_missing,
                headers=headers,
                options=options,
            )


__all__ = [
    "match_commit_prefix",
    "restore",
    "restore_chain_code",
    "restore_code",
    "restore_commit",
    "resolve_ref",
]
