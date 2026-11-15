"""Unit tests for source parsing, layout, and config loading."""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from support import make_temp_dir

from repoArchiver import layout
from repoArchiver.errors import ArchiveError, ConfigError


class SourceParsingTests(unittest.TestCase):
    def test_https_url(self) -> None:
        self.assertEqual(
            layout.archive_id_from_url("https://github.com/psf/requests.git"),
            "psf__requests",
        )
        self.assertEqual(
            layout.parse_source("https://github.com/psf/requests"),
            ("github.com", "psf", "requests"),
        )

    def test_scp_style_url(self) -> None:
        self.assertEqual(
            layout.archive_id_from_url("git@github.com:psf/requests.git"),
            "psf__requests",
        )

    def test_ssh_url_with_port(self) -> None:
        self.assertEqual(
            layout.parse_source("ssh://git@git.example.com:2222/acme/tool.git"),
            ("git.example.com", "acme", "tool"),
        )

    def test_trailing_slash_and_nested_group(self) -> None:
        self.assertEqual(
            layout.archive_id_from_url("https://gitlab.com/group/sub/tool.git/"),
            "sub__tool",
        )

    def test_local_directory(self) -> None:
        tmp = make_temp_dir("local-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = tmp / "demo"
        repo.mkdir()
        host, owner, name = layout.parse_source(str(repo))
        self.assertEqual(host, "")
        self.assertEqual(name, "demo")
        self.assertEqual(owner, tmp.name)

    def test_unparseable_source(self) -> None:
        with self.assertRaises(ConfigError):
            layout.parse_source("https://github.com/onlyowner")


class ArchiveIdTests(unittest.TestCase):
    def test_rejects_traversal(self) -> None:
        for bad in ("", ".", "..", "../x", "a/b", "a\\b"):
            with self.subTest(bad=bad), self.assertRaises(ArchiveError):
                layout.validate_archive_id(bad)

    def test_safe_component(self) -> None:
        self.assertEqual(layout.safe_component("my org/repo"), "my_org_repo")
        self.assertEqual(layout.safe_component("..."), "repo")


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_temp_dir("config-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_explicit_missing_config_fails(self) -> None:
        with self.assertRaises(ConfigError):
            layout.load_config(self.tmp / "nope.toml")

    def test_reads_values_and_resolves_relative_dir(self) -> None:
        config_path = self.tmp / "config.toml"
        config_path.write_text(
            'archives_dir = "archives"\ntoken = "secret"\n', encoding="utf-8"
        )
        previous = Path.cwd()
        os.chdir(self.tmp)
        try:
            config = layout.load_config(config_path)
        finally:
            os.chdir(previous)
        self.assertEqual(config.archives_dir, self.tmp / "archives")
        self.assertEqual(config.token, "secret")

    def test_invalid_toml_fails(self) -> None:
        config_path = self.tmp / "config.toml"
        config_path.write_text("archives_dir = [", encoding="utf-8")
        with self.assertRaises(ConfigError):
            layout.load_config(config_path)

    def test_token_falls_back_to_environment(self) -> None:
        config_path = self.tmp / "config.toml"
        config_path.write_text('archives_dir = "archives"\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "from-env"}, clear=False):
            config = layout.load_config(config_path)
        self.assertEqual(config.token, "from-env")


if __name__ == "__main__":
    unittest.main()
