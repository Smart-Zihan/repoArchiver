"""HTTP layer tests: retries, rate limits, resumable downloads (no network)."""

from __future__ import annotations

import io
import json
import shutil
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from support import make_temp_dir

from repoArchiver import http


class FakeResponse:
    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None) -> None:
        self._buffer = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self._buffer.close()
        return False


def http_error(
    code: int,
    headers: dict | None = None,
    body: bytes = b'{"message": "boom"}',
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.example.test/x", code, "error", headers or {}, io.BytesIO(body)
    )


class HttpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        http.reset_throttle()
        self.tmp = make_temp_dir("http-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def use(self, *results: object) -> mock.Mock:
        """Patch the single network seam with a sequence of results."""

        opener = mock.Mock(side_effect=list(results))
        patcher = mock.patch("repoArchiver.http._open", opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = mock.patch("repoArchiver.http.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        return opener


class GetJsonTests(HttpTestCase):
    def test_returns_body_and_headers(self) -> None:
        self.use(FakeResponse(json.dumps({"ok": True}).encode(), headers={"X-Test": "1"}))
        payload, headers = http.get_json("https://api.example.test/x")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers["X-Test"], "1")

    def test_retries_transient_server_errors(self) -> None:
        opener = self.use(http_error(500), FakeResponse(b'{"ok": 2}'))
        payload, _headers = http.get_json("https://api.example.test/x")
        self.assertEqual(payload, {"ok": 2})
        self.assertEqual(opener.call_count, 2)

    def test_client_errors_are_not_retried(self) -> None:
        opener = self.use(http_error(404, body=b'{"message": "Not Found"}'))
        with self.assertRaises(http.HttpError) as caught:
            http.get_json("https://api.example.test/x")
        self.assertEqual(caught.exception.status, 404)
        self.assertTrue(caught.exception.not_found)
        self.assertEqual(opener.call_count, 1)
        self.assertIn("Not Found", str(caught.exception))

    def test_rate_limit_with_a_long_reset_fails_fast_with_a_hint(self) -> None:
        import time

        headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(time.time()) + 3600),
        }
        opener = self.use(http_error(403, headers=headers))
        with self.assertRaises(http.HttpError) as caught:
            http.get_json("https://api.example.test/x")
        self.assertIn("rate limited", str(caught.exception))
        self.assertIn("token", str(caught.exception))
        self.assertEqual(opener.call_count, 1)

    def test_retries_network_failures(self) -> None:
        opener = self.use(
            urllib.error.URLError("connection reset"),
            FakeResponse(b"[]"),
        )
        payload, _headers = http.get_json("https://api.example.test/x")
        self.assertEqual(payload, [])
        self.assertEqual(opener.call_count, 2)

    def test_network_failure_surfaces_after_retries(self) -> None:
        options = http.HttpOptions(retries=1)
        opener = self.use(
            urllib.error.URLError("down"),
            urllib.error.URLError("down"),
        )
        with self.assertRaises(http.HttpError) as caught:
            http.get_json("https://api.example.test/x", options=options)
        self.assertIn("cannot reach", str(caught.exception))
        self.assertEqual(opener.call_count, 2)

    def test_invalid_json_is_an_http_error(self) -> None:
        self.use(FakeResponse(b"not json"))
        with self.assertRaises(http.HttpError):
            http.get_json("https://api.example.test/x")


class DownloadTests(HttpTestCase):
    def _part(self, target: Path) -> Path:
        return target.with_name(f".{target.name}.part")

    def test_writes_the_file_atomically(self) -> None:
        self.use(FakeResponse(b"hello"))
        target = self.tmp / "out.bin"
        http.download("https://example.test/f", target, expected_size=5)
        self.assertEqual(target.read_bytes(), b"hello")
        self.assertFalse(self._part(target).exists())

    def test_resumes_a_partial_download_with_a_range_request(self) -> None:
        opener = self.use(FakeResponse(b"world", status=206))
        target = self.tmp / "out.bin"
        self._part(target).write_bytes(b"hello")
        http.download("https://example.test/f", target, expected_size=10)
        self.assertEqual(target.read_bytes(), b"helloworld")
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=5-")

    def test_server_ignoring_range_restarts_the_file(self) -> None:
        self.use(FakeResponse(b"hello", status=200))
        target = self.tmp / "out.bin"
        self._part(target).write_bytes(b"hel")
        http.download("https://example.test/f", target, expected_size=5)
        self.assertEqual(target.read_bytes(), b"hello")

    def test_size_mismatch_keeps_the_partial_file_for_the_next_run(self) -> None:
        options = http.HttpOptions(retries=0)
        self.use(FakeResponse(b"abc"))
        target = self.tmp / "out.bin"
        with self.assertRaises(http.HttpError):
            http.download("https://example.test/f", target, options=options, expected_size=10)
        self.assertFalse(target.exists())
        self.assertEqual(self._part(target).read_bytes(), b"abc")

    def test_existing_complete_file_is_reused(self) -> None:
        opener = self.use()
        target = self.tmp / "out.bin"
        target.write_bytes(b"hello")
        http.download("https://example.test/f", target, expected_size=5)
        self.assertEqual(opener.call_count, 0)

    def test_leftovers_reports_part_files(self) -> None:
        (self.tmp / ".a.bin.part").write_bytes(b"x")
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / ".b.bin.part").write_bytes(b"x")
        found = http.leftovers(self.tmp)
        self.assertEqual(len(found), 2)


class ThrottleTests(unittest.TestCase):
    def setUp(self) -> None:
        http.reset_throttle()

    def test_min_interval_sleeps_between_requests(self) -> None:
        opener = mock.Mock(side_effect=[FakeResponse(b"{}"), FakeResponse(b"{}")])
        with mock.patch("repoArchiver.http._open", opener), mock.patch(
            "repoArchiver.http.time.sleep"
        ) as sleep, mock.patch("repoArchiver.http.time.monotonic", side_effect=[100.0, 100.0, 100.0, 100.0]):
            options = http.HttpOptions(min_interval=2.0)
            http.get_json("https://api.example.test/x", options=options)
            http.get_json("https://api.example.test/x", options=options)
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 2.0, places=3)


if __name__ == "__main__":
    unittest.main()
