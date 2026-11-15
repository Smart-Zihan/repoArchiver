"""History rewriting by cutoff, and annotated-tag preservation."""

from __future__ import annotations

import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout

from support import commit, make_temp_dir, run_git

from repoArchiver import layout
from repoArchiver.cli import main as cli_main

NEW_ENV = {"GIT_AUTHOR_DATE": "2025-06-01T00:00:00Z", "GIT_COMMITTER_DATE": "2025-06-01T00:00:00Z"}


class RewriteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("rewrite-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.repo = self.tmp / "demo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("old\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "old work", when="2020-01-01T00:00:00Z")
        self.old_commit = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()

        (self.repo / "a.txt").write_text("old\nnew\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        run_git(
            self.repo,
            "-c",
            "user.name=repoArchiver tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "new work",
            env=NEW_ENV,
        )
        self.new_commit = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()
        run_git(
            self.repo,
            "-c",
            "user.name=Tagger",
            "-c",
            "user.email=tagger@example.invalid",
            "tag",
            "-a",
            "v2",
            "-m",
            "release two",
            env=NEW_ENV,
        )

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
                "--rewrite-history",
                "--since",
                "2025-01-01",
                "-c",
                str(self.config),
                *extra,
            ]
        )

    def meta(self) -> dict:
        return json.loads((self.archive_dir / "meta.json").read_text(encoding="utf-8"))


class RewriteTests(RewriteTestCase):
    def test_rewrite_keeps_only_commits_after_the_cutoff(self) -> None:
        code, out, err = self.archive()
        self.assertEqual(code, 0, err)
        self.assertIn("rewrite", out)
        self.assertEqual(self.meta()["source_mode"], "rewrite")
        self.assertEqual(self.meta()["cutoff"]["since"], "2025-01-01")
        self.assertEqual(self.meta()["rewrite"]["commits"], 1)

        dest = self.tmp / "out"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "main", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual((dest / "a.txt").read_text(encoding="utf-8"), "old\nnew\n")
        self.assertEqual(len(run_git(dest, "log", "--oneline").stdout.strip().splitlines()), 1)
        self.assertEqual(run_git(dest, "rev-list", "--count", "HEAD").stdout.strip(), "1")

    def test_rewrite_changes_the_hash_and_records_the_old_tip(self) -> None:
        self.archive()
        tips = self.meta()["rewrite"]["refs"]
        self.assertEqual(tips["refs/heads/main"]["from"], self.new_commit)
        self.assertNotEqual(tips["refs/heads/main"]["to"], self.new_commit)
        self.assertEqual(tips["refs/tags/v2"]["from"], self.new_commit)

    def test_annotated_tag_keeps_its_tagger_and_message(self) -> None:
        self.archive()
        dest = self.tmp / "out-tag"
        code, _out, err = self.run_cli(
            ["restore", self.archive_id, "v2", "-d", str(dest), "-c", str(self.config)]
        )
        self.assertEqual(code, 0, err)

        tag = run_git(dest, "cat-file", "-p", "v2").stdout
        self.assertIn("tagger Tagger <tagger@example.invalid>", tag)
        self.assertIn("release two", tag)
        head = run_git(dest, "rev-parse", "HEAD").stdout.strip()
        tagged = run_git(dest, "rev-parse", "v2^{commit}").stdout.strip()
        self.assertEqual(head, tagged)

    def test_rewrite_requires_a_cutoff(self) -> None:
        code, _out, err = self.run_cli(
            [
                "archive",
                str(self.repo),
                "--only-code",
                "--rewrite-history",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("--rewrite-history needs a cutoff", err)

    def test_rewrite_conflicts_with_snapshot(self) -> None:
        code, _out, err = self.run_cli(
            [
                "archive",
                str(self.repo),
                "--only-code",
                "--rewrite-history",
                "--since",
                "2025-01-01",
                "--snapshot",
                "-c",
                str(self.config),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("either --snapshot or --rewrite-history", err)


if __name__ == "__main__":
    unittest.main()
