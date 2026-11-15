"""Ref selection rules and orphan-commit preservation."""

from __future__ import annotations

import shutil
import unittest

from support import commit, make_temp_dir, run_git

from repoArchiver import layout, refs
from repoArchiver.archiver import SCOPE_CODE, ArchiveRequest, run_archive
from repoArchiver.bundle import list_heads
from repoArchiver.errors import ArchiveError


class RefRulesTests(unittest.TestCase):
    def test_bare_patterns_match_branches_and_tags(self) -> None:
        rules = refs.RefRules(include=("main", "v*"))
        self.assertTrue(rules.allows("refs/heads/main"))
        self.assertTrue(rules.allows("refs/tags/v1.0.0"))
        self.assertFalse(rules.allows("refs/heads/other"))

    def test_full_ref_patterns_are_used_verbatim(self) -> None:
        rules = refs.RefRules(include=("refs/heads/release/*",))
        self.assertTrue(rules.allows("refs/heads/release/1.0"))
        self.assertFalse(rules.allows("refs/tags/release/1.0"))

    def test_excludes_win_over_includes(self) -> None:
        rules = refs.RefRules(include=("refs/heads/*",), exclude=("wip*",))
        self.assertTrue(rules.allows("refs/heads/main"))
        self.assertFalse(rules.allows("refs/heads/wip-experiment"))

    def test_internal_refs_are_never_selected_by_a_glob(self) -> None:
        rules = refs.RefRules(include=("refs/*",))
        self.assertFalse(rules.allows("refs/repoArchiver/orphans/" + "a" * 40))

    def test_json_round_trip_and_description(self) -> None:
        rules = refs.RefRules(include=("main",), exclude=("wip",), keep_orphans=True)
        restored = refs.RefRules.from_json(rules.to_json())
        self.assertEqual(restored, rules)
        self.assertIn("include=main", rules.describe())
        self.assertIn("orphans", rules.describe())
        self.assertTrue(refs.RefRules().is_default())


class RepoRefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("refs-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "one")
        run_git(self.repo, "tag", "v1")
        run_git(self.repo, "checkout", "--quiet", "-b", "wip")
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        run_git(self.repo, "add", "b.txt")
        commit(self.repo, "two")
        run_git(self.repo, "checkout", "--quiet", "main")

    def test_repo_refs_lists_branches_and_tags(self) -> None:
        names = [ref for _commit, ref in refs.repo_refs(str(self.repo))]
        self.assertEqual(
            names, ["refs/heads/main", "refs/heads/wip", "refs/tags/v1"]
        )

    def test_exclude_prunes_a_branch(self) -> None:
        rules = refs.RefRules(exclude=("wip",))
        selected = refs.select_for_bundle(str(self.repo), rules)
        self.assertEqual(
            [ref for _commit, ref in selected], ["refs/heads/main", "refs/tags/v1"]
        )

    def test_include_keeps_only_the_match(self) -> None:
        rules = refs.RefRules(include=("main",))
        selected = refs.select_for_bundle(str(self.repo), rules)
        self.assertEqual([ref for _commit, ref in selected], ["refs/heads/main"])

    def test_empty_selection_is_an_error(self) -> None:
        rules = refs.RefRules(include=("nothing-here",))
        with self.assertRaises(ArchiveError):
            refs.select_for_bundle(str(self.repo), rules)


class OrphanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("orphan-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run_git(self.repo, "init", "--quiet", "-b", "main")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        run_git(self.repo, "add", "a.txt")
        commit(self.repo, "one")
        run_git(self.repo, "checkout", "--quiet", "-b", "throwaway")
        (self.repo / "b.txt").write_text("lost work\n", encoding="utf-8")
        run_git(self.repo, "add", "b.txt")
        commit(self.repo, "lost")
        self.lost = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()
        run_git(self.repo, "checkout", "--quiet", "main")
        run_git(self.repo, "branch", "-D", "throwaway")

    def test_orphan_tip_is_detected(self) -> None:
        self.assertEqual(refs.orphan_tips(str(self.repo)), [self.lost])

    def test_pinning_adds_an_internal_ref(self) -> None:
        pinned = refs.install_orphan_refs(str(self.repo))
        self.assertEqual(
            pinned, [(self.lost, refs.ORPHAN_PREFIX + self.lost)]
        )

    def test_archive_with_keep_orphans_bundles_the_dangling_commit(self) -> None:
        config = layout.Config(archives_dir=self.tmp / "archives")
        result = run_archive(
            ArchiveRequest(
                source=str(self.repo),
                scope=SCOPE_CODE,
                refs=refs.RefRules(keep_orphans=True),
            ),
            config,
        )
        heads = dict(
            (ref, commit_hash) for commit_hash, ref in list_heads(result.bundle_path)
        )
        orphan_ref = refs.ORPHAN_PREFIX + self.lost
        self.assertIn(orphan_ref, heads)
        self.assertEqual(heads[orphan_ref], self.lost)
        self.assertEqual(result.orphans, 1)
        source_refs = run_git(self.repo, "for-each-ref", "--format=%(refname)").stdout
        self.assertNotIn("repoArchiver", source_refs)


if __name__ == "__main__":
    unittest.main()
