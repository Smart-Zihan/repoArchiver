"""Tests for archive scope (code / releases), asset selection, sync and trace."""

from __future__ import annotations

import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from support import commit, make_temp_dir, run_git

from repoArchiver import layout
from repoArchiver.bundle import list_heads
from repoArchiver.cli import main as cli_main
from repoArchiver.releases import normalize_release

REMOTE_URL = "https://github.com/acme/demo"
REMOTE_ID = "acme__demo"


def github_release(tag: str, assets: tuple[str, ...] = (), published: str = "2024-01-01T00:00:00Z") -> dict:
    """Build a raw GitHub API release payload (pre-normalization)."""

    return {
        "tag_name": tag,
        "name": tag,
        "body": "",
        "published_at": published,
        "prerelease": False,
        "draft": False,
        "zipball_url": f"https://api.github.com/repos/acme/demo/zipball/{tag}",
        "tarball_url": f"https://api.github.com/repos/acme/demo/tarball/{tag}",
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://example.invalid/acme/demo/{tag}/{name}",
                "size": len(name),
            }
            for name in assets
        ],
    }


def normalized(*payloads: dict, repo: str = "demo", owner: str = "acme") -> list[dict]:
    return [normalize_release(payload, repo, owner) for payload in payloads]


def fake_download(
    url: str,
    target: str,
    token: str | None = None,
    expected_size: int | None = None,
    **kwargs: object,
) -> Path:
    """Stand-in for download_asset: write bytes locally, touch no network."""

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (expected_size if expected_size is not None else 8))
    return path


class ScopeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("scope-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.repo = self.tmp / "demo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "one")
        self.first_commit = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()
        run_git(self.repo, "tag", "v1.0.0")

        self.config_path = self.tmp / "config.toml"
        self.config_path.write_text(
            f'archives_dir = "{(self.tmp / "archives").as_posix()}"\n',
            encoding="utf-8",
        )
        self.local_id = layout.archive_id_from_url(str(self.repo))
        self.local_dir = self.tmp / "archives" / self.local_id
        self.remote_dir = self.tmp / "archives" / REMOTE_ID

    def run_cli(self, args: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def read_json(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    def index(self, directory: Path) -> list[dict]:
        return self.read_json(directory / "releases" / "index.json")  # type: ignore[return-value]

    def meta(self, directory: Path) -> dict:
        return self.read_json(directory / "meta.json")  # type: ignore[return-value]

    def state(self, directory: Path) -> dict:
        return self.read_json(directory / "state.json")  # type: ignore[return-value]

    def archive_releases(self, *payloads: dict, extra: tuple[str, ...] = ()) -> tuple[int, str, str]:
        """Archive REMOTE_URL as releases-only with a mocked API."""

        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            return_value=normalized(*payloads),
        ), mock.patch(
            "repoArchiver.releases.download_asset",
            side_effect=fake_download,
        ):
            return self.run_cli(
                ["archive", REMOTE_URL, "--only-releases", *extra, "-c", str(self.config_path)]
            )


class ReleasesOnlyTests(ScopeTestCase):
    def test_stores_metadata_and_every_asset(self) -> None:
        code, out, err = self.archive_releases(
            github_release("v1.0.0", ("notes.txt", "demo-linux-x86_64.tar.gz")),
            extra=("-t", "nightly"),
        )
        self.assertEqual(code, 0, err)
        self.assertIn("releases only", out)

        self.assertFalse((self.remote_dir / "code.bundle").exists())
        self.assertFalse((self.tmp / "archives" / ".mirrors").exists())

        meta = self.meta(self.remote_dir)
        self.assertEqual(meta["scope"], "releases")
        self.assertIsNone(meta["bundle"])
        self.assertEqual(meta["refs_count"], 0)
        self.assertEqual(meta["release_count"], 1)
        self.assertTrue(meta["with_assets"])

        index = self.index(self.remote_dir)
        self.assertEqual([release["tag_name"] for release in index], ["v1.0.0"])
        assets = {asset["name"]: asset for asset in index[0]["assets"]}
        self.assertEqual(
            set(assets),
            {
                "notes.txt",
                "demo-linux-x86_64.tar.gz",
                "demo-v1.0.0.zip",
                "demo-v1.0.0.tar.gz",
            },
        )
        for asset in assets.values():
            self.assertTrue(asset["local_path"], asset["name"])
            self.assertTrue(Path(asset["local_path"]).is_file())
        self.assertEqual(assets["notes.txt"]["kind"], "asset")
        self.assertEqual(assets["demo-v1.0.0.zip"]["kind"], "source")
        self.assertEqual(assets["demo-v1.0.0.tar.gz"]["kind"], "source")

        state = self.state(self.remote_dir)
        self.assertEqual(state["tracker"], "nightly")
        self.assertEqual(state["scope"], "releases")

    def test_never_invokes_git(self) -> None:
        with mock.patch(
            "repoArchiver.archiver.create_bundle",
            side_effect=AssertionError("git must not be used for releases-only archives"),
        ), mock.patch(
            "repoArchiver.archiver.mirror_source",
            side_effect=AssertionError("git must not be used for releases-only archives"),
        ):
            code, _out, err = self.archive_releases(github_release("v1.0.0"))
        self.assertEqual(code, 0, err)

    def test_no_assets_keeps_metadata_only(self) -> None:
        with mock.patch(
            "repoArchiver.releases.download_asset",
            side_effect=AssertionError("--no-assets must not download"),
        ), mock.patch(
            "repoArchiver.archiver.fetch_releases",
            return_value=normalized(github_release("v1.0.0", ("notes.txt",))),
        ):
            code, _out, err = self.run_cli(
                [
                    "archive",
                    REMOTE_URL,
                    "--only-releases",
                    "--no-assets",
                    "-c",
                    str(self.config_path),
                ]
            )
        self.assertEqual(code, 0, err)
        self.assertFalse(self.meta(self.remote_dir)["with_assets"])
        self.assertFalse((self.remote_dir / "releases" / "assets").exists())
        assets = self.index(self.remote_dir)[0]["assets"]
        self.assertTrue(assets)
        self.assertTrue(all(asset["local_path"] is None for asset in assets))

    def test_no_source_skips_source_archives(self) -> None:
        code, _out, err = self.archive_releases(
            github_release("v1.0.0", ("notes.txt",)), extra=("--no-source",)
        )
        self.assertEqual(code, 0, err)
        assets = {asset["name"]: asset for asset in self.index(self.remote_dir)[0]["assets"]}
        self.assertIn("demo-v1.0.0.zip", assets)
        self.assertIsNone(assets["demo-v1.0.0.zip"]["local_path"])
        self.assertIsNone(assets["demo-v1.0.0.tar.gz"]["local_path"])
        self.assertTrue(assets["notes.txt"]["local_path"])

    def test_skip_asset_glob(self) -> None:
        code, _out, err = self.archive_releases(
            github_release("v1.0.0", ("notes.txt",)),
            extra=("--skip-asset", "*.tar.gz"),
        )
        self.assertEqual(code, 0, err)
        saved = {
            asset["name"]
            for asset in self.index(self.remote_dir)[0]["assets"]
            if asset["local_path"]
        }
        self.assertIn("demo-v1.0.0.zip", saved)
        self.assertNotIn("demo-v1.0.0.tar.gz", saved)
        self.assertIn("notes.txt", saved)

    def test_release_glob_limits_downloads_but_keeps_metadata(self) -> None:
        code, _out, err = self.archive_releases(
            github_release("v1.0.0"),
            github_release("v2.0.0", published="2024-06-01T00:00:00Z"),
            extra=("--release", "v2*"),
        )
        self.assertEqual(code, 0, err)
        index = {release["tag_name"]: release for release in self.index(self.remote_dir)}
        self.assertEqual(set(index), {"v1.0.0", "v2.0.0"})
        self.assertTrue(all(asset["local_path"] is None for asset in index["v1.0.0"]["assets"]))
        self.assertTrue(all(asset["local_path"] for asset in index["v2.0.0"]["assets"]))

    def test_source_archives_use_codeload_and_avoid_name_clashes(self) -> None:
        code, _out, err = self.archive_releases(
            github_release("v1.0.0", ("demo-v1.0.0.zip",))
        )
        self.assertEqual(code, 0, err)
        assets = {asset["name"]: asset for asset in self.index(self.remote_dir)[0]["assets"]}
        self.assertEqual(assets["demo-v1.0.0.zip"]["kind"], "asset")
        generated = assets["demo-v1.0.0-source.zip"]
        self.assertEqual(generated["kind"], "source")
        self.assertEqual(
            generated["url"],
            "https://codeload.github.com/acme/demo/zip/refs/tags/v1.0.0",
        )

    def test_since_tag_works_without_a_repository(self) -> None:
        code, _out, err = self.archive_releases(
            github_release("v1.0.0", published="2023-01-01T00:00:00Z"),
            github_release("v2.0.0", published="2025-01-01T00:00:00Z"),
            extra=("--since-tag", "2.0.0"),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            [release["tag_name"] for release in self.index(self.remote_dir)], ["v2.0.0"]
        )
        self.assertEqual(self.meta(self.remote_dir)["cutoff"]["since_tag"], "v2.0.0")

    def test_unknown_since_tag_fails_without_a_repository(self) -> None:
        code, _out, err = self.archive_releases(
            github_release("v1.0.0"), extra=("--since-tag", "v9.9.9")
        )
        self.assertEqual(code, 1)
        self.assertIn("not found in the release list", err)

    def test_rearchive_reuses_downloaded_files(self) -> None:
        calls: list[str] = []

        def counting_download(
            url: str,
            target: str,
            token: str | None = None,
            expected_size: int | None = None,
            **kwargs: object,
        ) -> Path:
            calls.append(Path(target).name)
            return fake_download(url, target, token, expected_size)

        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            return_value=normalized(github_release("v1.0.0", ("notes.txt",))),
        ), mock.patch("repoArchiver.releases.download_asset", side_effect=counting_download):
            self.run_cli(
                ["archive", REMOTE_URL, "--only-releases", "-c", str(self.config_path)]
            )
            first_run = len(calls)
            self.run_cli(
                ["archive", REMOTE_URL, "--only-releases", "-c", str(self.config_path)]
            )

        self.assertGreater(first_run, 0)
        self.assertEqual(len(calls), first_run)


class ReleasesOnlyRestoreTests(ScopeTestCase):
    def test_restore_assets_of_a_code_less_archive(self) -> None:
        self.archive_releases(github_release("v1.0.0", ("notes.txt",)))
        dest = self.tmp / "out-release"
        code, _out, err = self.run_cli(
            ["--no-trace", "restore", REMOTE_ID, "v1.0.0", "-d", str(dest), "-c", str(self.config_path)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("stores no code", err)
        self.assertTrue((dest / "assets" / "notes.txt").is_file())
        self.assertTrue((dest / "assets" / "demo-v1.0.0.tar.gz").is_file())
        self.assertFalse((dest / ".git").exists())

    def test_restore_unknown_tag_reports_missing_code(self) -> None:
        self.archive_releases(github_release("v1.0.0"))
        code, _out, err = self.run_cli(
            [
                "--no-trace",
                "restore",
                REMOTE_ID,
                "v9",
                "-d",
                str(self.tmp / "out-unknown"),
                "-c",
                str(self.config_path),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("stores no code", err)

    def test_restore_only_restores_archived_assets(self) -> None:
        self.archive_releases(
            github_release("v1.0.0", ("notes.txt",)), extra=("--skip-asset", "*.tar.gz")
        )
        dest = self.tmp / "out-partial"
        code, _out, err = self.run_cli(
            ["--no-trace", "restore", REMOTE_ID, "v1.0.0", "-d", str(dest), "-c", str(self.config_path)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("not stored in this archive", err)
        self.assertTrue((dest / "assets" / "notes.txt").is_file())
        self.assertTrue((dest / "assets" / "demo-v1.0.0.zip").is_file())
        self.assertFalse((dest / "assets" / "demo-v1.0.0.tar.gz").exists())

    def test_fetch_missing_downloads_the_rest(self) -> None:
        self.archive_releases(
            github_release("v1.0.0", ("notes.txt",)), extra=("--skip-asset", "*.tar.gz")
        )
        dest = self.tmp / "out-fetch"
        with mock.patch("repoArchiver.restore.download_asset", side_effect=fake_download):
            code, _out, err = self.run_cli(
                [
                    "--no-trace",
                    "restore",
                    REMOTE_ID,
                    "v1.0.0",
                    "-d",
                    str(dest),
                    "--fetch-missing",
                    "-c",
                    str(self.config_path),
                ]
            )
        self.assertEqual(code, 0, err)
        self.assertTrue((dest / "assets" / "demo-v1.0.0.tar.gz").is_file())

    def test_assets_only_on_a_code_archive(self) -> None:
        code, _out, err = self.run_cli(
            ["--no-trace", "archive", str(self.repo), "-c", str(self.config_path)]
        )
        self.assertEqual(code, 0, err)

        asset_dir = self.local_dir / "releases" / "assets" / "v1.0.0"
        asset_dir.mkdir(parents=True)
        (asset_dir / "notes.txt").write_text("hi\n", encoding="utf-8")
        (self.local_dir / "releases" / "index.json").write_text(
            json.dumps(
                [
                    {
                        "tag_name": "v1.0.0",
                        "name": "v1.0.0",
                        "body": "",
                        "published_at": None,
                        "prerelease": False,
                        "draft": False,
                        "commit_hash": self.first_commit,
                        "assets": [
                            {
                                "name": "notes.txt",
                                "url": None,
                                "size": 3,
                                "local_path": "releases/assets/v1.0.0/notes.txt",
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )

        dest = self.tmp / "only-assets"
        code, _out, err = self.run_cli(
            [
                "--no-trace",
                "restore",
                self.local_id,
                "v1.0.0",
                "-d",
                str(dest),
                "--assets-only",
                "-c",
                str(self.config_path),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((dest / "assets" / "notes.txt").is_file())
        self.assertFalse((dest / ".git").exists())
        self.assertFalse((dest / "a.txt").exists())


class CodeOnlyTests(ScopeTestCase):
    def test_never_calls_the_release_api(self) -> None:
        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            side_effect=AssertionError("release API must not be queried"),
        ):
            code, out, err = self.run_cli(
                ["archive", str(self.repo), "--only-code", "-c", str(self.config_path)]
            )
        self.assertEqual(code, 0, err)
        self.assertIn("code only", out)

        meta = self.meta(self.local_dir)
        self.assertEqual(meta["scope"], "code")
        self.assertEqual(meta["release_count"], 0)
        self.assertFalse(meta["with_assets"])
        self.assertTrue((self.local_dir / "code.bundle").is_file())
        self.assertFalse((self.local_dir / "releases" / "index.json").exists())

        _code, listing, _err = self.run_cli(
            ["--no-trace", "list", self.local_id, "-c", str(self.config_path)]
        )
        self.assertIn("refs/heads/main", listing)
        self.assertIn("releases: 0", listing)

    def test_snapshot_with_only_releases_is_rejected(self) -> None:
        code, _out, err = self.run_cli(
            ["archive", REMOTE_URL, "--only-releases", "--snapshot", "-c", str(self.config_path)]
        )
        self.assertEqual(code, 1)
        self.assertIn("--only-releases", err)

    def test_with_assets_with_only_code_is_rejected(self) -> None:
        code, _out, err = self.run_cli(
            ["archive", str(self.repo), "--only-code", "--with-assets", "-c", str(self.config_path)]
        )
        self.assertEqual(code, 1)
        self.assertIn("--only-code", err)


class SyncTests(ScopeTestCase):
    def test_downloads_new_releases_and_logs_history(self) -> None:
        self.archive_releases(github_release("v1.0.0", ("notes.txt",)), extra=("-t", "nightly"))

        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            return_value=normalized(
                github_release("v1.0.0", ("notes.txt",)),
                github_release("v2.0.0", ("notes-v2.txt",), published="2024-06-01T00:00:00Z"),
            ),
        ), mock.patch("repoArchiver.releases.download_asset", side_effect=fake_download):
            code, out, err = self.run_cli(["sync", REMOTE_ID, "-c", str(self.config_path)])

        self.assertEqual(code, 0, err)
        self.assertIn("+1 release(s)", out)
        index = self.index(self.remote_dir)
        self.assertEqual([release["tag_name"] for release in index], ["v1.0.0", "v2.0.0"])
        self.assertTrue(all(asset["local_path"] for asset in index[1]["assets"]))

        history = [
            json.loads(line)
            for line in (self.remote_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(history[-1]["event"], "sync")
        self.assertEqual(history[-1]["added_releases"], ["v2.0.0"])
        self.assertTrue(self.state(self.remote_dir)["last_sync_at"])

    def test_dry_run_writes_nothing(self) -> None:
        self.archive_releases(github_release("v1.0.0", ("notes.txt",)), extra=("-t", "nightly"))
        before = (self.remote_dir / "releases" / "index.json").read_text(encoding="utf-8")

        with mock.patch(
            "repoArchiver.track.fetch_releases",
            return_value=normalized(
                github_release("v1.0.0", ("notes.txt",)),
                github_release("v2.0.0", published="2024-06-01T00:00:00Z"),
            ),
        ):
            code, out, err = self.run_cli(
                ["sync", REMOTE_ID, "--dry-run", "-c", str(self.config_path)]
            )

        self.assertEqual(code, 0, err)
        self.assertIn("would sync", out)
        self.assertIn("+1 release(s)", out)
        self.assertEqual(
            (self.remote_dir / "releases" / "index.json").read_text(encoding="utf-8"), before
        )
        self.assertFalse((self.remote_dir / "history.jsonl").exists())
        self.assertFalse((self.remote_dir / "releases" / "assets" / "v2.0.0").exists())

    def test_untracked_archive_is_an_error(self) -> None:
        self.run_cli(["--no-trace", "archive", str(self.repo), "-c", str(self.config_path)])
        code, _out, err = self.run_cli(["sync", self.local_id, "-c", str(self.config_path)])
        self.assertEqual(code, 1)
        self.assertIn("not tracked", err)

    def test_no_tracked_archives_only_warns(self) -> None:
        code, _out, err = self.run_cli(["sync", "-c", str(self.config_path)])
        self.assertEqual(code, 0)
        self.assertIn("no tracked archives", err)

    def test_dry_run_plans_new_commits(self) -> None:
        code, _out, err = self.run_cli(
            [
                "--no-trace",
                "archive",
                str(self.repo),
                "--only-code",
                "-t",
                "nightly",
                "-c",
                str(self.config_path),
            ]
        )
        self.assertEqual(code, 0, err)
        bundle_before = (self.local_dir / "code.bundle").read_bytes()

        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        run_git(self.repo, "add", "b.txt")
        commit(self.repo, "two")

        code, out, err = self.run_cli(
            ["sync", self.local_id, "--dry-run", "-c", str(self.config_path)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("would sync", out)
        self.assertIn("ref/commit", out)
        self.assertEqual((self.local_dir / "code.bundle").read_bytes(), bundle_before)
        self.assertFalse((self.local_dir / "history.jsonl").exists())

    def test_unchanged_code_archive_is_not_rebuilt(self) -> None:
        code, _out, err = self.run_cli(
            [
                "--no-trace",
                "archive",
                str(self.repo),
                "--only-code",
                "-t",
                "nightly",
                "-c",
                str(self.config_path),
            ]
        )
        self.assertEqual(code, 0, err)

        with mock.patch(
            "repoArchiver.archiver.create_bundle",
            side_effect=AssertionError("an unchanged code-only archive must not be rebuilt"),
        ):
            code, out, err = self.run_cli(["sync", self.local_id, "-c", str(self.config_path)])
        self.assertEqual(code, 0, err)
        self.assertIn("up to date", out)
        self.assertTrue(self.state(self.local_dir)["last_sync_at"])

    def test_code_scope_bundles_new_commits(self) -> None:
        code, _out, err = self.run_cli(
            [
                "--no-trace",
                "archive",
                str(self.repo),
                "--only-code",
                "-t",
                "nightly",
                "-c",
                str(self.config_path),
            ]
        )
        self.assertEqual(code, 0, err)

        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        run_git(self.repo, "add", "b.txt")
        commit(self.repo, "two")
        new_head = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()

        code, out, err = self.run_cli(["sync", self.local_id, "-c", str(self.config_path)])
        self.assertEqual(code, 0, err)
        self.assertIn("ref/commit", out)

        refs = {ref: commit_hash for commit_hash, ref in list_heads(self.local_dir / "code.bundle")}
        self.assertEqual(refs["refs/heads/main"], new_head)

        history = [
            json.loads(line)
            for line in (self.local_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        added = {entry["ref"] for entry in history[-1]["added_refs"]}
        self.assertIn("refs/heads/main", added)


class TraceTests(ScopeTestCase):
    def test_trace_refreshes_a_tracked_archive_on_list(self) -> None:
        self.archive_releases(github_release("v1.0.0", ("notes.txt",)), extra=("-t", "nightly"))

        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            return_value=normalized(
                github_release("v1.0.0", ("notes.txt",)),
                github_release("v2.0.0", published="2024-06-01T00:00:00Z"),
            ),
        ), mock.patch("repoArchiver.releases.download_asset", side_effect=fake_download):
            code, out, err = self.run_cli(["list", REMOTE_ID, "-c", str(self.config_path)])

        self.assertEqual(code, 0, err)
        self.assertIn("trace acme__demo: +1 release(s)", out)
        self.assertEqual(len(self.index(self.remote_dir)), 2)

        history = [
            json.loads(line)
            for line in (self.remote_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(history[-1]["event"], "trace")

    def test_no_trace_flag_skips_the_refresh(self) -> None:
        self.archive_releases(github_release("v1.0.0"), extra=("-t", "nightly"))
        before = (self.remote_dir / "releases" / "index.json").read_text(encoding="utf-8")

        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            side_effect=AssertionError("--no-trace must not refresh"),
        ):
            code, out, err = self.run_cli(
                ["--no-trace", "list", REMOTE_ID, "-c", str(self.config_path)]
            )
        self.assertEqual(code, 0, err)
        self.assertNotIn("trace ", out)
        self.assertEqual(
            (self.remote_dir / "releases" / "index.json").read_text(encoding="utf-8"), before
        )

    def test_config_can_disable_trace(self) -> None:
        self.archive_releases(github_release("v1.0.0"), extra=("-t", "nightly"))
        quiet_config = self.tmp / "quiet.toml"
        quiet_config.write_text(
            f'archives_dir = "{(self.tmp / "archives").as_posix()}"\ntrace = false\n',
            encoding="utf-8",
        )
        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            side_effect=AssertionError("trace disabled in config"),
        ):
            code, out, err = self.run_cli(
                ["list", REMOTE_ID, "-c", str(quiet_config)]
            )
        self.assertEqual(code, 0, err)
        self.assertNotIn("trace ", out)

    def test_local_archives_are_not_traced(self) -> None:
        code, _out, err = self.run_cli(
            [
                "--no-trace",
                "archive",
                str(self.repo),
                "--only-code",
                "-t",
                "nightly",
                "-c",
                str(self.config_path),
            ]
        )
        self.assertEqual(code, 0, err)
        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            side_effect=AssertionError("local sources are never traced"),
        ):
            code, out, err = self.run_cli(
                ["list", self.local_id, "-c", str(self.config_path)]
            )
        self.assertEqual(code, 0, err)
        self.assertNotIn("trace ", out)


class InspectTests(ScopeTestCase):
    def test_inspect_shows_scope_selection_and_history(self) -> None:
        self.archive_releases(
            github_release("v1.0.0", ("notes.txt",)),
            extra=("-t", "nightly", "--skip-asset", "*.zip"),
        )
        with mock.patch(
            "repoArchiver.archiver.fetch_releases",
            return_value=normalized(
                github_release("v1.0.0", ("notes.txt",)),
                github_release("v2.0.0", published="2024-06-01T00:00:00Z"),
            ),
        ), mock.patch("repoArchiver.releases.download_asset", side_effect=fake_download):
            self.run_cli(["sync", REMOTE_ID, "-c", str(self.config_path)])

        code, out, err = self.run_cli(
            ["--no-trace", "inspect", REMOTE_ID, "-c", str(self.config_path)]
        )
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"scope:\s+releases")
        self.assertRegex(out, r"bundle:\s+none")
        self.assertIn("selection:", out)
        self.assertIn("skip-assets=*.zip", out)
        self.assertIn("last_sync_at", out)
        self.assertIn("history:", out)


if __name__ == "__main__":
    unittest.main()
