"""Platform tests: payload normalization per forge (no network)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from repoArchiver.errors import ConfigError
from repoArchiver.http import HttpError
from repoArchiver.platforms import detect_platform, platform_for
from repoArchiver.releases import fetch_releases


def assets_of(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {asset["name"]: asset for asset in release.get("assets") or []}


class PlatformTestCase(unittest.TestCase):
    def fetch(
        self,
        source: str,
        responses: list[tuple[str, Any, dict]],
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run fetch_releases with get_json replaced by a table lookup."""

        def fake_get_json(self_platform: object, url: str) -> tuple[Any, dict]:
            for needle, payload, headers in responses:
                if needle in url:
                    return payload, headers
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch(
            "repoArchiver.platforms.base.Platform.get_json", new=fake_get_json
        ):
            return fetch_releases(source, token="t", platform=platform)


class DetectionTests(unittest.TestCase):
    def test_known_hosts(self) -> None:
        self.assertEqual(detect_platform("github.com"), "github")
        self.assertEqual(detect_platform("gitlab.com"), "gitlab")
        self.assertEqual(detect_platform("codeberg.org"), "gitea")
        self.assertEqual(detect_platform("bitbucket.org"), "bitbucket")
        self.assertEqual(detect_platform("gitlab.example.org"), "gitlab")
        self.assertEqual(detect_platform("gitea.example.org"), "gitea")
        self.assertIsNone(detect_platform("git.example.org"))

    def test_platform_override_is_validated(self) -> None:
        with self.assertRaises(ConfigError):
            platform_for("https://git.example.org/a/b", name="subversion")
        impl = platform_for("https://git.example.org/a/b", name="gitea")
        self.assertEqual(impl.name, "Gitea")

    def test_unknown_host_yields_the_unsupported_platform(self) -> None:
        impl = platform_for("https://git.example.org/a/b")
        self.assertEqual(impl.name, "unsupported")


class GitHubTests(PlatformTestCase):
    PAYLOAD = [
        {
            "tag_name": "v1.0.0",
            "name": "1.0.0",
            "body": "notes",
            "published_at": "2024-05-20T14:00:00Z",
            "prerelease": False,
            "draft": False,
            "zipball_url": "https://api.github.com/repos/acme/demo/zipball/v1.0.0",
            "tarball_url": "https://api.github.com/repos/acme/demo/tarball/v1.0.0",
            "assets": [
                {
                    "name": "demo-v1.0.0.zip",
                    "browser_download_url": "https://example.test/demo-v1.0.0.zip",
                    "size": 12,
                }
            ],
        }
    ]

    def test_normalizes_assets_and_source_archives(self) -> None:
        releases = self.fetch(
            "https://github.com/acme/demo",
            [("/releases?", self.PAYLOAD, {})],
        )
        self.assertEqual(len(releases), 1)
        release = releases[0]
        self.assertEqual(release["tag_name"], "v1.0.0")
        self.assertEqual(release["body"], "notes")
        assets = assets_of(release)
        self.assertEqual(assets["demo-v1.0.0.zip"]["kind"], "asset")
        self.assertEqual(
            assets["demo-v1.0.0.zip"]["url"], "https://example.test/demo-v1.0.0.zip"
        )
        generated = assets["demo-v1.0.0-source.zip"]
        self.assertEqual(generated["kind"], "source")
        self.assertIn("codeload.github.com/acme/demo/zip", generated["url"])
        self.assertIn("demo-v1.0.0.tar.gz", assets)

    def test_follows_the_link_header(self) -> None:
        page2 = [dict(self.PAYLOAD[0], tag_name="v2.0.0")]
        responses = [
            ("&page=1", self.PAYLOAD, {"Link": '<https://api.github.com/x?page=2>; rel="next"'}),
            ("&page=2", page2, {}),
        ]
        releases = self.fetch("https://github.com/acme/demo", responses)
        self.assertEqual([release["tag_name"] for release in releases], ["v1.0.0", "v2.0.0"])

    def test_not_found_only_warns(self) -> None:
        with mock.patch("repoArchiver.platforms.base.warn") as warned, mock.patch(
            "repoArchiver.platforms.base.Platform.get_json",
            side_effect=HttpError("HTTP 404", status=404),
        ):
            self.assertEqual(fetch_releases("https://github.com/acme/demo"), [])
        warned.assert_called_once()
        self.assertIn("404", warned.call_args.args[0])


class GitLabTests(PlatformTestCase):
    PAYLOAD = [
        {
            "tag_name": "v2.0.0",
            "name": "2.0.0",
            "description": "desc",
            "released_at": "2024-06-01T00:00:00.000Z",
            "upcoming_release": False,
            "commit": {"id": "abc123"},
            "assets": {
                "links": [
                    {
                        "name": "pkg.deb",
                        "url": "https://gitlab.com/acme/demo/-/releases/v2.0.0/downloads/pkg.deb",
                        "direct_asset_url": "https://gitlab.com/acme/demo/-/releases/v2.0.0/downloads/direct/pkg.deb",
                    }
                ],
                "sources": [
                    {"format": "zip", "url": "https://gitlab.com/acme/demo/-/archive/v2.0.0/demo.zip"},
                    {"format": "tar.gz", "url": "https://gitlab.com/acme/demo/-/archive/v2.0.0/demo.tar.gz"},
                    {"format": "tar.bz2", "url": "https://gitlab.com/acme/demo/-/archive/v2.0.0/demo.tar.bz2"},
                ],
            },
        }
    ]

    def test_normalizes_links_sources_and_commit(self) -> None:
        releases = self.fetch("https://gitlab.com/acme/demo", [("/releases?", self.PAYLOAD, {})])
        release = releases[0]
        self.assertEqual(release["commit_hash"], "abc123")
        self.assertEqual(release["body"], "desc")
        self.assertEqual(release["published_at"], "2024-06-01T00:00:00.000Z")
        assets = assets_of(release)
        self.assertEqual(assets["pkg.deb"]["url"], self.PAYLOAD[0]["assets"]["links"][0]["direct_asset_url"])
        self.assertEqual(assets["pkg.deb"]["kind"], "asset")
        self.assertEqual(assets["demo-v2.0.0.zip"]["kind"], "source")
        self.assertIn("demo-v2.0.0.tar.gz", assets)
        self.assertIn("demo-v2.0.0.tar.bz2", assets)

    def test_paginates_with_the_next_page_header(self) -> None:
        page2 = [dict(self.PAYLOAD[0], tag_name="v3.0.0")]
        releases = self.fetch(
            "https://gitlab.com/acme/demo",
            [
                ("&page=1", self.PAYLOAD, {"X-Next-Page": "2"}),
                ("&page=2", page2, {"X-Next-Page": ""}),
            ],
        )
        self.assertEqual([release["tag_name"] for release in releases], ["v2.0.0", "v3.0.0"])


class GiteaTests(PlatformTestCase):
    PAYLOAD = [
        {
            "tag_name": "v3.0.0",
            "name": "3.0.0",
            "body": "notes",
            "published_at": "2024-07-01T00:00:00Z",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "demo.exe",
                    "browser_download_url": "https://codeberg.org/acme/demo/releases/download/v3.0.0/demo.exe",
                    "size": 99,
                },
                {"name": "broken", "browser_download_url": "", "size": 0},
            ],
        }
    ]

    def test_normalizes_assets_and_archive_urls(self) -> None:
        releases = self.fetch("https://codeberg.org/acme/demo", [("/releases?", self.PAYLOAD, {})])
        release = releases[0]
        self.assertTrue(release["prerelease"])
        assets = assets_of(release)
        self.assertEqual(assets["demo.exe"]["size"], 99)
        self.assertIsNone(assets["broken"]["url"])
        self.assertEqual(assets["demo-v3.0.0.zip"]["kind"], "source")
        self.assertEqual(
            assets["demo-v3.0.0.zip"]["url"],
            "https://codeberg.org/acme/demo/archive/v3.0.0.zip",
        )


class BitbucketTests(PlatformTestCase):
    TAGS = {
        "values": [
            {
                "name": "v4.0.0",
                "target": {
                    "hash": "beef1234",
                    "date": "2024-08-01T00:00:00+00:00",
                    "message": "tagged",
                },
            }
        ],
        "next": None,
    }
    DOWNLOADS = {
        "values": [
            {
                "name": "setup.exe",
                "size": 5,
                "links": {"self": {"href": "https://bitbucket.org/acme/demo/downloads/setup.exe"}},
            }
        ],
        "next": None,
    }

    def test_tags_and_downloads_become_releases(self) -> None:
        releases = self.fetch(
            "https://bitbucket.org/acme/demo",
            [("/refs/tags", self.TAGS, {}), ("/downloads", self.DOWNLOADS, {})],
        )
        by_tag = {release["tag_name"]: release for release in releases}
        self.assertIn("v4.0.0", by_tag)
        tag_release = by_tag["v4.0.0"]
        self.assertEqual(tag_release["commit_hash"], "beef1234")
        self.assertEqual(tag_release["body"], "tagged")
        self.assertEqual(
            assets_of(tag_release)["demo-v4.0.0.zip"]["url"],
            "https://bitbucket.org/acme/demo/get/v4.0.0.zip",
        )

        self.assertIn("downloads", by_tag)
        downloads = assets_of(by_tag["downloads"])
        self.assertEqual(
            downloads["setup.exe"]["url"],
            "https://bitbucket.org/acme/demo/downloads/setup.exe",
        )
        self.assertIsNone(by_tag["downloads"]["published_at"])


if __name__ == "__main__":
    unittest.main()
