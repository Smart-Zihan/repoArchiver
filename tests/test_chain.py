"""Incremental bundle chains: append, replay, compact."""

from __future__ import annotations

import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout

from support import commit, make_temp_dir, run_git

from repoArchiver import layout
from repoArchiver.cli import main as cli_main


class ChainTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("chain-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.repo = self.tmp / "demo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "one")
        run_git(self.repo, "tag", "v1")

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

    def archive(self, *extra: str) -> tuple[int, str, str]:
        return self.run_cli(
            [
                "archive",
                str(self.repo),
                "--only-code",
                "--incremental",
                "-t",
                "demo",
                "-c",
                str(self.config),
                *extra,
            ]
        )

    def commit_more(self, text: str) -> str:
        with (self.repo / "a.txt").open("a", encoding="utf-8") as handle:
            handle.write(text)
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, text.strip())
        return run_git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def bundles(self) -> list[str]:
        return sorted(path.name for path in self.archive_dir.glob("code*.bundle"))

    def meta(self) -> dict:
        return json.loads((self.archive_dir / "meta.json").read_text(encoding="utf-8"))


class ChainTests(ChainTestCase):
    def test_second_archive_appends_an_increment(self) -> None:
        code, _out, err = self.archive()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bundles(), ["code.bundle"])

        new_commit = self.commit_more("two\n")
        code, out, err = self.archive()
        self.assertEqual(code, 0, err)
        self.assertIn("incremental", out)

        bundles = self.bundles()
        increments = [name for name in bundles if name.startswith("code-")]
        self.assertEqual(len(bundles), 2)
        self.assertEqual(len(increments), 1)
        chain = self.meta()["bundle_chain"]
        self.assertEqual([item["kind"] for item in chain], ["base", "incremental"])

        code, out, err = self.run_cli(["list", self.archive_id, "-c", str(self.config)])
        self.assertEqual(code, 0, err)
        self.assertIn(new_commit[:12], out)

    def test_unchanged_archive_adds_no_bundle(self) -> None:
        self.archive()
        code, _out, err = self.archive()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bundles(), ["code.bundle"])

    def test_sync_appends_an_increment(self) -> None:
        self.archive()
        self.commit_more("two\n")
        code, out, err = self.run_cli(["sync", self.archive_id, "-c", str(self.config)])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.bundles()), 2)
        self.assertIn("synced", out)

    def test_restore_replays_the_whole_chain(self) -> None:
        self.archive()
        self.commit_more("two\n")
        self.archive()

        dest = self.tmp / "out"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "main", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual((dest / "a.txt").read_text(encoding="utf-8"), "one\ntwo\n")
        log = run_git(dest, "log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(log), 2)

    def test_verify_accepts_a_chain(self) -> None:
        self.archive()
        self.commit_more("two\n")
        self.archive()
        code, out, err = self.run_cli(
            ["verify", self.archive_id, "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("ok", out)

    def test_compact_folds_the_chain(self) -> None:
        self.archive()
        self.commit_more("two\n")
        self.archive()
        self.assertEqual(len(self.bundles()), 2)

        code, out, err = self.run_cli(
            ["compact", self.archive_id, "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("2 bundle(s) -> code.bundle", out)
        self.assertEqual(self.bundles(), ["code.bundle"])
        chain = self.meta()["bundle_chain"]
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["kind"], "base")

        dest = self.tmp / "out-compact"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "main", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual((dest / "a.txt").read_text(encoding="utf-8"), "one\ntwo\n")

    def test_dropping_a_ref_falls_back_to_a_full_bundle(self) -> None:
        code, _out, err = self.archive("--include-ref", "refs/heads/main")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.meta()["refs_count"], 1)

        code, _out, err = self.archive("--include-ref", "refs/tags/*")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bundles(), ["code.bundle"])
        self.assertEqual(self.meta()["refs_count"], 1)
        self.assertIn("refs_rules", self.meta())


if __name__ == "__main__":
    unittest.main()
