"""Tests for the progress reporter and the message catalogue.

These are pure unit tests: no git, no network, no temporary repositories.
"""

from __future__ import annotations

import io
import unittest

from repoArchiver import i18n, progress


class LanguageTests(unittest.TestCase):
    def tearDown(self) -> None:
        i18n.set_language("en")

    def test_aliases_map_to_supported_languages(self) -> None:
        for value in ("zh", "zh-CN", "zh_CN", "zh-Hans", "cn", "Chinese"):
            self.assertEqual(i18n.normalize_language(value), "zh", value)
        for value in ("en", "en-US", "EN_gb", "english"):
            self.assertEqual(i18n.normalize_language(value), "en", value)

    def test_unknown_language_is_rejected(self) -> None:
        self.assertIsNone(i18n.normalize_language("klingon"))
        self.assertIsNone(i18n.normalize_language(""))
        self.assertIsNone(i18n.normalize_language(None))

    def test_unknown_language_falls_back_to_english(self) -> None:
        self.assertEqual(i18n.set_language("klingon"), "en")

    def test_flag_wins_over_config(self) -> None:
        self.assertEqual(
            i18n.resolve_language(flag="zh", config="en", env=False), "zh"
        )
        self.assertEqual(
            i18n.resolve_language(flag=None, config="zh", env=False), "zh"
        )
        self.assertEqual(i18n.resolve_language(flag=None, config=None, env=False), "en")

    def test_translation_and_placeholders(self) -> None:
        i18n.set_language("zh")
        text = i18n.tr('ref "{ref}" not found', ref="v0")
        self.assertIn("v0", text)
        self.assertIn("未找到", text)

    def test_missing_translation_keeps_english(self) -> None:
        i18n.set_language("zh")
        message = "a message no catalogue knows about"
        self.assertEqual(i18n.tr(message), message)

    def test_placeholder_mismatch_does_not_raise(self) -> None:
        i18n.set_language("zh")
        self.assertIsInstance(i18n.tr('ref "{ref}" not found'), str)

    def test_every_translation_has_matching_placeholders(self) -> None:
        """A translation with a different placeholder set silently falls back."""

        import string

        for source, translated in i18n._catalog_zh().items():
            source_fields = {
                name for _text, name, _spec, _conv in string.Formatter().parse(source) if name
            }
            translated_fields = {
                name
                for _text, name, _spec, _conv in string.Formatter().parse(translated)
                if name
            }
            self.assertEqual(
                source_fields,
                translated_fields,
                f"placeholder mismatch for: {source}",
            )


class GitProgressTests(unittest.TestCase):
    def test_parses_phase_and_percentage(self) -> None:
        self.assertEqual(
            progress.parse_git_progress("Receiving objects:  45% (100/220)"),
            ("Receiving objects", 45),
        )
        self.assertEqual(
            progress.parse_git_progress("remote: Counting objects: 100% (9/9), done."),
            ("Counting objects", 100),
        )
        self.assertEqual(
            progress.parse_git_progress("Writing objects: 7% (1/14)"),
            ("Writing objects", 7),
        )

    def test_ignores_lines_without_percentage(self) -> None:
        self.assertIsNone(progress.parse_git_progress("Enumerating objects: 1234, done."))
        self.assertIsNone(progress.parse_git_progress(""))
        self.assertIsNone(progress.parse_git_progress("Cloning into bare repository..."))

    def test_clamps_out_of_range_percentages(self) -> None:
        phase, percent = progress.parse_git_progress("Resolving deltas: 250%")
        self.assertEqual((phase, percent), ("Resolving deltas", 100))


class ReporterTests(unittest.TestCase):
    def test_never_mode_stays_silent(self) -> None:
        stream = io.StringIO()
        reporter = progress.Reporter(progress.NEVER, stream)
        reporter.stage("hello")
        reporter.git("Receiving objects: 50% (5/10)")
        reporter.close()
        self.assertEqual(stream.getvalue(), "")

    def test_plain_mode_reports_stage_and_final_milestone(self) -> None:
        stream = io.StringIO()
        reporter = progress.Reporter(progress.ALWAYS, stream)
        reporter.stage("writing code.bundle")
        for percent in (10, 20, 40, 60, 80, 100):
            reporter.git(f"Writing objects: {percent}% ({percent}/100)")
        reporter.close()
        output = stream.getvalue()
        self.assertIn("→ writing code.bundle", output)
        self.assertIn("Writing objects 100%", output)

    def test_verbose_forwards_raw_lines(self) -> None:
        stream = io.StringIO()
        reporter = progress.Reporter(progress.ALWAYS, stream, verbose=True)
        reporter.git("remote: Enumerating objects: 12, done.")
        reporter.close()
        self.assertIn("remote: Enumerating objects: 12, done.", stream.getvalue())

    def test_no_heartbeat_without_a_terminal(self) -> None:
        stream = io.StringIO()
        reporter = progress.Reporter(progress.ALWAYS, stream)
        with reporter.long_stage("cloning"):
            pass
        self.assertIsNone(reporter._thread)
        self.assertIn("→ cloning", stream.getvalue())

    def test_download_bar_needs_a_terminal(self) -> None:
        stream = io.StringIO()
        reporter = progress.Reporter(progress.ALWAYS, stream)
        reporter.download("asset.zip", 5, 10)
        self.assertEqual(stream.getvalue(), "")

    def test_format_size(self) -> None:
        self.assertEqual(progress.format_size(0), "0 B")
        self.assertEqual(progress.format_size(1536), "1.5 KiB")
        self.assertEqual(progress.format_size(5 * 1024 * 1024), "5.0 MiB")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
