"""Unit tests for cutoff parsing/filtering and snapshot ref selection."""

from __future__ import annotations

import io
import shutil
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime

from support import commit, make_temp_dir, run_git

from repoArchiver.cutoff import (
    Cutoff,
    filter_releases,
    parse_instant,
    resolve_cutoff,
    select_refs,
)
from repoArchiver.errors import ArchiveError


class ParseInstantTests(unittest.TestCase):
    def test_date_only(self) -> None:
        parsed = parse_instant("2024-01-01")
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2024, 1, 1))

    def test_utc_suffix(self) -> None:
        parsed = parse_instant("2024-01-01T00:00:00Z")
        self.assertEqual(parsed, datetime(2024, 1, 1, tzinfo=UTC))

    def test_offset(self) -> None:
        parsed = parse_instant("2024-01-01T08:00:00+08:00")
        self.assertEqual(
            parsed.astimezone(UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        )

    def test_invalid(self) -> None:
        with self.assertRaises(ArchiveError):
            parse_instant("yesterday-ish")

    def test_empty(self) -> None:
        with self.assertRaises(ArchiveError):
            parse_instant("  ")


class FilterReleasesTests(unittest.TestCase):
    def test_keeps_releases_at_or_after_cutoff(self) -> None:
        cutoff = Cutoff(parse_instant("2024-01-01T00:00:00Z"), label="2024-01-01")
        releases = [
            {"tag_name": "old", "published_at": "2023-12-31T23:59:59Z"},
            {"tag_name": "boundary", "published_at": "2024-01-01T00:00:00Z"},
            {"tag_name": "new", "published_at": "2024-06-01T00:00:00Z"},
            {"tag_name": "draft", "published_at": None, "draft": True},
            {"tag_name": "broken", "published_at": "not-a-date"},
        ]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            kept = filter_releases(releases, cutoff)
        self.assertEqual([release["tag_name"] for release in kept], ["boundary", "new"])
        self.assertIn("skipped 2 release(s)", stderr.getvalue())


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("cutoff-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("new\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "new", when="2024-03-01T00:00:00+00:00")
        run_git(self.repo, "tag", "v1.2.3")

        run_git(self.repo, "checkout", "--quiet", "-b", "legacy")
        (self.repo / "old.txt").write_text("old\n", encoding="utf-8")
        run_git(self.repo, "add", "old.txt")
        commit(self.repo, "old", when="2020-01-01T00:00:00+00:00")
        run_git(self.repo, "tag", "legacy-v1")
        run_git(self.repo, "checkout", "--quiet", "main")


class ResolveCutoffTests(RepoTestCase):
    def test_since(self) -> None:
        cutoff = resolve_cutoff(self.repo, [], since="2024-01-01")
        self.assertIsNotNone(cutoff)
        self.assertEqual(cutoff.instant, parse_instant("2024-01-01"))
        self.assertEqual(cutoff.since, "2024-01-01")

    def test_since_tag_prefers_release_date(self) -> None:
        releases = [
            {"tag_name": "v1.2.3", "published_at": "2024-05-01T12:00:00Z"}
        ]
        cutoff = resolve_cutoff(self.repo, releases, since_tag="v1.2.3")
        self.assertIsNotNone(cutoff)
        self.assertEqual(cutoff.instant, datetime(2024, 5, 1, 12, tzinfo=UTC))
        self.assertEqual(cutoff.since_tag, "v1.2.3")

    def test_since_tag_falls_back_to_commit_date(self) -> None:
        cutoff = resolve_cutoff(self.repo, [], since_tag="1.2.3")
        self.assertIsNotNone(cutoff)
        self.assertEqual(cutoff.since_tag, "v1.2.3")
        self.assertEqual(cutoff.instant, datetime(2024, 3, 1, tzinfo=UTC))

    def test_since_tag_missing(self) -> None:
        with self.assertRaises(ArchiveError):
            resolve_cutoff(self.repo, [], since_tag="v0.0.1")

    def test_no_cutoff(self) -> None:
        self.assertIsNone(resolve_cutoff(self.repo, []))


class SelectRefsTests(RepoTestCase):
    def test_cutoff_drops_old_refs(self) -> None:
        cutoff = Cutoff(parse_instant("2024-01-01T00:00:00Z"), label="2024-01-01")
        refs = select_refs(self.repo, cutoff)
        names = [ref for _commit, ref in refs]
        self.assertEqual(names, ["refs/heads/main", "refs/tags/v1.2.3"])
        self.assertTrue(all(len(commit_hash) == 40 for commit_hash, _ref in refs))

    def test_no_cutoff_keeps_every_ref(self) -> None:
        names = [ref for _commit, ref in select_refs(self.repo, None)]
        self.assertEqual(
            names,
            [
                "refs/heads/legacy",
                "refs/heads/main",
                "refs/tags/legacy-v1",
                "refs/tags/v1.2.3",
            ],
        )


if __name__ == "__main__":
    unittest.main()
