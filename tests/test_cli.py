"""End-to-end CLI tests using a local git repository (no network access)."""

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
from repoArchiver.cli import main as cli_main


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.repo = self.tmp / "demo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "one")
        self.tag_commit = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()
        run_git(self.repo, "tag", "v1.0.0")

        run_git(self.repo, "checkout", "--quiet", "-b", "feature")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        run_git(self.repo, "add", "b.txt")
        commit(self.repo, "two")
        run_git(self.repo, "checkout", "--quiet", "main")

        self.config = self.tmp / "config.toml"
        self.config.write_text(
            f'archives_dir = "{(self.tmp / "archives").as_posix()}"\n',
            encoding="utf-8",
        )
        self.archive_id = layout.archive_id_from_url(str(self.repo))
        self.archive_dir = self.tmp / "archives" / self.archive_id

    def run_cli(self, args: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def archive(self) -> str:
        code, _out, err = self.run_cli(
            ["archive", str(self.repo), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        return err

    def write_index(self, releases: list[dict[str, object]]) -> None:
        index_path = self.archive_dir / "releases" / "index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(releases), encoding="utf-8")


class ArchiveCommandTests(CliTestCase):
    def test_archive_writes_bundle_meta_and_state(self) -> None:
        code, out, err = self.run_cli(
            ["archive", str(self.repo), "-c", str(self.config), "-t", "nightly"]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("archived", out)
        self.assertIn("local repository source", err)

        bundle = self.archive_dir / "code.bundle"
        self.assertTrue(bundle.is_file())
        self.assertGreater(bundle.stat().st_size, 0)

        meta = json.loads((self.archive_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["source"], str(self.repo))
        self.assertEqual(meta["bundle"], "code.bundle")
        self.assertEqual(meta["release_count"], 0)
        self.assertGreaterEqual(meta["refs_count"], 3)

        state = json.loads((self.archive_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["tracker"], "nightly")

    def test_archive_without_tracker_does_not_write_state(self) -> None:
        self.archive()
        self.assertFalse((self.archive_dir / "state.json").exists())

    def test_archive_updates_existing(self) -> None:
        self.archive()
        code, _out, err = self.run_cli(
            ["archive", str(self.repo), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("updating existing archive", err)


class ListCommandTests(CliTestCase):
    def test_list_merges_refs_and_releases(self) -> None:
        self.archive()
        self.write_index(
            [
                {
                    "tag_name": "v9.9.9",
                    "name": "v9.9.9",
                    "body": "",
                    "published_at": None,
                    "prerelease": False,
                    "draft": False,
                    "commit_hash": self.tag_commit,
                    "assets": [],
                }
            ]
        )
        code, out, _err = self.run_cli(["list", self.archive_id, "-c", str(self.config)])
        self.assertEqual(code, 0)
        self.assertIn("REF", out)
        self.assertIn("refs/heads/main", out)
        self.assertIn("refs/tags/v1.0.0", out)
        self.assertIn("v9.9.9", out)

    def test_list_ref_prefix_filter(self) -> None:
        self.archive()
        code, out, _err = self.run_cli(
            ["list", self.archive_id, "-r", "refs/tags/", "-c", str(self.config)]
        )
        self.assertEqual(code, 0)
        self.assertIn("refs/tags/v1.0.0", out)
        self.assertNotIn("refs/heads/main", out)

    def test_list_short_flag(self) -> None:
        self.archive()
        code, out, _err = self.run_cli(["-l", self.archive_id, "-c", str(self.config)])
        self.assertEqual(code, 0)
        self.assertIn("refs/heads/main", out)

    def test_list_missing_archive(self) -> None:
        code, _out, err = self.run_cli(["list", "nobody__nothing", "-c", str(self.config)])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)


class RestoreCommandTests(CliTestCase):
    def test_restore_tag_and_branch(self) -> None:
        self.archive()

        tag_dest = self.tmp / "out-tag"
        code, out, err = self.run_cli(
            ["restore", self.archive_id, "v1.0.0", "-d", str(tag_dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("restored", out)
        self.assertEqual(
            run_git(tag_dest, "rev-parse", "HEAD").stdout.strip(), self.tag_commit
        )
        self.assertTrue((tag_dest / "a.txt").is_file())
        self.assertFalse((tag_dest / "b.txt").exists())
        self.assertEqual(
            run_git(tag_dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "HEAD"
        )

        branch_dest = self.tmp / "out-branch"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "main", "-d", str(branch_dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            run_git(branch_dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main"
        )
        self.assertFalse((branch_dest / "b.txt").exists())

    def test_restore_unknown_ref(self) -> None:
        self.archive()
        dest = self.tmp / "out-missing"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "v0", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 1)
        self.assertIn('ref "v0" not found', err)
        self.assertFalse(dest.exists())

    def test_dest_not_empty_requires_force(self) -> None:
        self.archive()
        dest = self.tmp / "out-force"
        dest.mkdir()
        (dest / "keep.txt").write_text("keep\n", encoding="utf-8")

        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "v1.0.0", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 1)
        self.assertIn("not empty", err)
        self.assertTrue((dest / "keep.txt").is_file())

        code, _out, err = self.run_cli(
            [
                "restore",
                self.archive_id,
                "v1.0.0",
                "-d",
                str(dest),
                "--force",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertFalse((dest / "keep.txt").exists())
        self.assertTrue((dest / "a.txt").is_file())

    def test_ambiguous_short_ref(self) -> None:
        run_git(self.repo, "tag", "main")
        self.archive()
        dest = self.tmp / "out-ambiguous"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "main", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 1)
        self.assertIn("ambiguous ref", err)
        self.assertIn("refs/heads/main", err)
        self.assertIn("refs/tags/main", err)

    def test_release_fallback_and_strict(self) -> None:
        self.archive()
        self.write_index(
            [
                {
                    "tag_name": "v9.9.9",
                    "name": "v9.9.9",
                    "body": "",
                    "published_at": None,
                    "prerelease": False,
                    "draft": False,
                    "commit_hash": self.tag_commit,
                    "assets": [],
                }
            ]
        )

        dest = self.tmp / "out-fallback"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "v9.9.9", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("not in bundle, falling back to commit", err)
        self.assertEqual(
            run_git(dest, "rev-parse", "HEAD").stdout.strip(), self.tag_commit
        )

        strict_dest = self.tmp / "out-strict"
        code, _out, err = self.run_cli(
            [
                "restore",
                self.archive_id,
                "v9.9.9",
                "-d",
                str(strict_dest),
                "--strict",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("--strict", err)
        self.assertFalse(strict_dest.exists())

    def test_with_assets_copies_local_files(self) -> None:
        self.archive()
        asset_dir = self.archive_dir / "releases" / "assets" / "v1.0.0"
        asset_dir.mkdir(parents=True)
        (asset_dir / "checksums.txt").write_text("sha256 abc\n", encoding="utf-8")
        self.write_index(
            [
                {
                    "tag_name": "v1.0.0",
                    "name": "v1.0.0",
                    "body": "",
                    "published_at": None,
                    "prerelease": False,
                    "draft": False,
                    "commit_hash": self.tag_commit,
                    "assets": [
                        {
                            "name": "checksums.txt",
                            "url": None,
                            "size": 11,
                            "local_path": "releases/assets/v1.0.0/checksums.txt",
                        }
                    ],
                }
            ]
        )

        dest = self.tmp / "out-assets"
        code, _out, err = self.run_cli(
            [
                "restore",
                self.archive_id,
                "v1.0.0",
                "-d",
                str(dest),
                "--with-assets",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((dest / "assets" / "checksums.txt").is_file())

    def test_restore_rejects_current_directory(self) -> None:
        self.archive()
        import os

        previous = Path.cwd()
        os.chdir(self.tmp)
        try:
            code, _out, err = self.run_cli(
                ["restore", self.archive_id, "v1.0.0", "-d", ".", "-c", str(self.config)]
            )
        finally:
            os.chdir(previous)
        self.assertEqual(code, 1)
        self.assertIn("current working directory", err)


class InspectCommandTests(CliTestCase):
    def test_inspect_prints_summary(self) -> None:
        self.archive()
        self.write_index(
            [
                {
                    "tag_name": "v1.0.0",
                    "name": "Release one",
                    "body": "",
                    "published_at": "2024-01-01T00:00:00Z",
                    "prerelease": False,
                    "draft": False,
                    "commit_hash": self.tag_commit,
                    "assets": [],
                }
            ]
        )
        code, out, _err = self.run_cli(
            ["inspect", self.archive_id, "-c", str(self.config)]
        )
        self.assertEqual(code, 0)
        self.assertIn("archive:", out)
        self.assertIn(str(self.repo), out)
        self.assertIn("tracker state: none", out)
        self.assertIn("v1.0.0", out)


class CutoffCommandTests(CliTestCase):
    def make_legacy_branch(self) -> None:
        """Add a 2020 branch/tag that a cutoff should drop."""

        run_git(self.repo, "checkout", "--quiet", "-b", "legacy")
        (self.repo / "old.txt").write_text("old\n", encoding="utf-8")
        run_git(self.repo, "add", "old.txt")
        commit(self.repo, "old", when="2020-01-01T00:00:00+00:00")
        run_git(self.repo, "tag", "legacy-v1")
        run_git(self.repo, "checkout", "--quiet", "main")

    def meta(self) -> dict[str, object]:
        return json.loads((self.archive_dir / "meta.json").read_text(encoding="utf-8"))

    def test_snapshot_since_filters_refs_and_history(self) -> None:
        self.make_legacy_branch()
        code, out, err = self.run_cli(
            [
                "archive",
                str(self.repo),
                "--snapshot",
                "--since",
                "2024-01-01",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("(snapshot, cutoff 2024-01-01)", out)

        meta = self.meta()
        self.assertEqual(meta["source_mode"], "snapshot")
        self.assertEqual(meta["cutoff"]["since"], "2024-01-01")
        self.assertEqual(meta["refs_count"], 3)

        _code, listing, _err = self.run_cli(
            ["list", self.archive_id, "-c", str(self.config)]
        )
        self.assertIn("refs/heads/main", listing)
        self.assertNotIn("legacy", listing)

        dest = self.tmp / "snapshot-main"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "main", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(run_git(dest, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        self.assertTrue((dest / "a.txt").is_file())
        self.assertFalse((dest / "old.txt").exists())
        self.assertIn(
            "repoArchiver snapshot of refs/heads/main",
            run_git(dest, "log", "-1", "--format=%s").stdout,
        )

        old_dest = self.tmp / "snapshot-legacy"
        code, _out, err = self.run_cli(
            [
                "restore",
                self.archive_id,
                "legacy-v1",
                "-d",
                str(old_dest),
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_snapshot_without_cutoff_keeps_every_ref(self) -> None:
        self.make_legacy_branch()
        self.assertEqual(
            self.run_cli(
                ["archive", str(self.repo), "--snapshot", "-c", str(self.config)]
            )[0],
            0,
        )
        meta = self.meta()
        self.assertEqual(meta["refs_count"], 5)
        self.assertNotIn("cutoff", meta)

        dest = self.tmp / "snapshot-legacy"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "legacy", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((dest / "old.txt").is_file())
        self.assertEqual(run_git(dest, "rev-list", "--count", "HEAD").stdout.strip(), "1")

    def test_since_tag_accepts_bare_version(self) -> None:
        self.make_legacy_branch()
        code, _out, err = self.run_cli(
            [
                "archive",
                str(self.repo),
                "--snapshot",
                "--since-tag",
                "1.0.0",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 0, err)
        meta = self.meta()
        self.assertEqual(meta["cutoff"]["since_tag"], "v1.0.0")
        self.assertEqual(meta["refs_count"], 3)

    def test_unknown_since_tag_fails(self) -> None:
        code, _out, err = self.run_cli(
            [
                "archive",
                str(self.repo),
                "--snapshot",
                "--since-tag",
                "v9.9.9",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_cutoff_without_snapshot_keeps_history(self) -> None:
        self.make_legacy_branch()
        code, _out, err = self.run_cli(
            ["archive", str(self.repo), "--since", "2024-01-01", "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("history is kept in full", err)
        meta = self.meta()
        self.assertNotIn("source_mode", meta)
        self.assertEqual(meta["refs_count"], 5)

        _code, inspect_out, _err = self.run_cli(
            ["inspect", self.archive_id, "-c", str(self.config)]
        )
        self.assertIn("cutoff:", inspect_out)

    def test_release_metadata_filtered_by_cutoff(self) -> None:
        releases = [
            {
                "tag_name": "v1.0.0",
                "name": "",
                "body": "",
                "published_at": "2023-01-01T00:00:00Z",
                "prerelease": False,
                "draft": False,
                "commit_hash": None,
                "assets": [],
            },
            {
                "tag_name": "v2.0.0",
                "name": "",
                "body": "",
                "published_at": "2025-01-01T00:00:00Z",
                "prerelease": False,
                "draft": False,
                "commit_hash": None,
                "assets": [],
            },
        ]
        with mock.patch("repoArchiver.archiver.fetch_releases", return_value=releases):
            code, out, err = self.run_cli(
                [
                    "archive",
                    str(self.repo),
                    "--snapshot",
                    "--since",
                    "2024-01-01",
                    "-c",
                    str(self.config),
                ]
            )
        self.assertEqual(code, 0, err)
        self.assertIn("kept 1 of 2 release(s)", out)
        index = json.loads(
            (self.archive_dir / "releases" / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual([release["tag_name"] for release in index], ["v2.0.0"])
        self.assertEqual(self.meta()["release_count"], 1)

    def test_invalid_since_date_fails(self) -> None:
        code, _out, err = self.run_cli(
            ["archive", str(self.repo), "--since", "not-a-date", "-c", str(self.config)]
        )
        self.assertEqual(code, 1)
        self.assertIn("invalid date", err)


if __name__ == "__main__":
    unittest.main()
