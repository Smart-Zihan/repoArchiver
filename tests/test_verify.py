"""verify findings and exit codes."""

from __future__ import annotations

import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout

from support import commit, make_temp_dir, run_git

from repoArchiver import layout
from repoArchiver.cli import main as cli_main


class VerifyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("verify-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.repo = self.tmp / "demo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "one")

        self.config = self.tmp / "config.toml"
        self.config.write_text(
            f'archives_dir = "{(self.tmp / "archives").as_posix()}"\ntrace = false\n',
            encoding="utf-8",
        )
        self.archive_id = layout.archive_id_from_url(str(self.repo))
        self.archive_dir = self.tmp / "archives" / self.archive_id

    def run_cli(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli_main(args)
        return code, out.getvalue(), err.getvalue()

    def archive(self) -> None:
        code, _out, err = self.run_cli(
            ["archive", str(self.repo), "--only-code", "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)

    def meta(self) -> dict:
        return json.loads((self.archive_dir / "meta.json").read_text(encoding="utf-8"))

    def write_meta(self, meta: dict) -> None:
        (self.archive_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def verify(self, *extra: str) -> tuple[int, str, str]:
        return self.run_cli(["verify", self.archive_id, "-c", str(self.config), *extra])


class VerifyTests(VerifyTestCase):
    def test_a_healthy_archive_passes(self) -> None:
        self.archive()
        code, out, err = self.verify()
        self.assertEqual(code, 0, err)
        self.assertIn("ok", out)
        self.assertIn("0 error(s)", out)

    def test_missing_bundle_is_an_error(self) -> None:
        self.archive()
        (self.archive_dir / "code.bundle").unlink()
        code, out, _err = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", out)
        self.assertIn("missing bundle", out)

    def test_missing_asset_is_an_error(self) -> None:
        self.archive()
        (self.archive_dir / "releases").mkdir(exist_ok=True)
        (self.archive_dir / "releases" / "index.json").write_text(
            json.dumps(
                [
                    {
                        "tag_name": "v1",
                        "name": "v1",
                        "body": "",
                        "published_at": None,
                        "prerelease": False,
                        "draft": False,
                        "commit_hash": None,
                        "assets": [
                            {
                                "name": "pkg.zip",
                                "url": "https://example.test/pkg.zip",
                                "size": 3,
                                "local_path": str(self.archive_dir / "releases" / "assets" / "v1" / "pkg.zip"),
                                "kind": "asset",
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        code, out, _err = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("asset-missing", out)

    def test_size_mismatch_is_an_error(self) -> None:
        self.archive()
        asset = self.archive_dir / "releases" / "assets" / "v1" / "pkg.zip"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"abc")
        (self.archive_dir / "releases" / "index.json").write_text(
            json.dumps(
                [
                    {
                        "tag_name": "v1",
                        "name": "v1",
                        "body": "",
                        "published_at": None,
                        "prerelease": False,
                        "draft": False,
                        "commit_hash": None,
                        "assets": [
                            {
                                "name": "pkg.zip",
                                "url": "https://example.test/pkg.zip",
                                "size": 999,
                                "local_path": str(asset),
                                "kind": "asset",
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        code, out, _err = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("asset-size", out)

    def test_refs_count_mismatch_is_only_a_warning(self) -> None:
        self.archive()
        meta = self.meta()
        meta["refs_count"] = 99
        self.write_meta(meta)

        code, out, _err = self.verify()
        self.assertEqual(code, 0)
        self.assertIn("WARNING", out)
        self.assertIn("refs-count", out)

        code, _out, _err = self.verify("--strict")
        self.assertEqual(code, 1)

    def test_json_output_is_machine_readable(self) -> None:
        self.archive()
        code, out, _err = self.verify("--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["archives"][0]["archive"], self.archive_id)

    def test_unfinished_download_is_reported(self) -> None:
        self.archive()
        (self.archive_dir / "releases" / "assets" / "v1").mkdir(parents=True)
        (
            self.archive_dir / "releases" / "assets" / "v1" / ".pkg.zip.part"
        ).write_bytes(b"partial")
        code, out, _err = self.verify()
        self.assertEqual(code, 0)
        self.assertIn("partial-downloads", out)

    def test_no_archives_is_not_an_error(self) -> None:
        code, _out, err = self.run_cli(["verify", "-c", str(self.config)])
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
