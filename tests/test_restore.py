"""Unit tests for ref resolution rules."""

from __future__ import annotations

import unittest

from repoArchiver.errors import AmbiguousRef, RefNotFound
from repoArchiver.restore import resolve_ref

REFS = [
    ("a" * 40, "refs/heads/main"),
    ("b" * 40, "refs/heads/feature/login"),
    ("c" * 40, "refs/tags/v1.0.0"),
    ("d" * 40, "refs/remotes/origin/main"),
]


class ResolveRefTests(unittest.TestCase):
    def test_full_name_wins(self) -> None:
        self.assertEqual(resolve_ref("refs/heads/main", REFS), "refs/heads/main")

    def test_unique_tag_leaf(self) -> None:
        self.assertEqual(resolve_ref("v1.0.0", REFS), "refs/tags/v1.0.0")

    def test_suffix_form(self) -> None:
        self.assertEqual(resolve_ref("heads/feature/login", REFS), "refs/heads/feature/login")
        self.assertEqual(resolve_ref("tags/v1.0.0", REFS), "refs/tags/v1.0.0")

    def test_branch_and_remote_branch_are_ambiguous(self) -> None:
        with self.assertRaises(AmbiguousRef) as ctx:
            resolve_ref("main", REFS)
        self.assertIn("refs/heads/main", ctx.exception.candidates)
        self.assertIn("refs/remotes/origin/main", ctx.exception.candidates)
        self.assertIn("ambiguous ref", str(ctx.exception))

    def test_missing_ref(self) -> None:
        with self.assertRaises(RefNotFound):
            resolve_ref("nope", REFS)

    def test_empty_ref(self) -> None:
        with self.assertRaises(RefNotFound):
            resolve_ref("   ", REFS)


if __name__ == "__main__":
    unittest.main()
