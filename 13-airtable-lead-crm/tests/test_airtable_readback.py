"""Trusted Airtable read-back tests; fake responses only, no sockets."""

from base64 import b64encode
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote, quote_plus


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("airtable_readback", ROOT / "tools/airtable_readback.py")
READBACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READBACK)
TOKEN = "patDUMMYc046NotReal.0123456789abcdef0123456789abcdef"
URL = "https://api.airtable.com:443/v0/appHNWJpExA6nWZks/tbly57I1Q5JJUyMlP?pageSize=100"


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.secret = self.root / "dummy-secret.txt"
        self.secret.write_text("# dummy only\n\nAIRTABLE_TOKEN=" + TOKEN + "\n")
        self.secret.chmod(0o600)
        self.out = self.root / "readback.json"
        self.stderr = io.StringIO()
        patcher = mock.patch.object(READBACK, "urlopen", side_effect=AssertionError("network forbidden"))
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self):
        with contextlib.redirect_stderr(self.stderr):
            return READBACK.main(["--secret-file", str(self.secret), "--out", str(self.out)])

    def assert_bad_secret(self, path=None):
        with self.assertRaisesRegex(ValueError, "^invalid secret file$"):
            READBACK.read_secret_file(self.secret if path is None else path)
        self.urlopen.assert_not_called()

    def test_get_headers_pagination_sorting_and_metadata_dropped(self):
        self.urlopen.side_effect = [response({"records": [
            {"id": "recB", "createdTime": "time", "fields": {"Email": "b@example.com", "Score": 10}}],
            "offset": "itr/a +?&"}), response({"records": [
                {"id": "recA", "fields": {"Email": "a@example.com"}}]})]
        self.assertEqual(self.invoke(), 0)
        for call, url in zip(self.urlopen.call_args_list, (URL, URL + "&offset=itr%2Fa%20%2B%3F%26")):
            request = call.args[0]
            self.assertEqual(request.full_url, url)
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(dict(request.header_items()), {"Authorization": "Bearer " + TOKEN})
            self.assertIsNone(request.data)
            self.assertEqual(call.kwargs, {"timeout": 30})
        expected = {"leads": [{"Email": "a@example.com"}, {"Email": "b@example.com", "Score": 10}]}
        self.assertEqual(json.loads(self.out.read_text()), expected)
        self.assertEqual(self.out.read_text(), json.dumps(expected, indent=2, sort_keys=True) + "\n")
        self.assertEqual(self.stderr.getvalue(), "")

    def test_empty_and_ten_pages_without_offset_on_last_are_valid(self):
        self.urlopen.side_effect = [response({"records": []})]
        self.assertEqual(READBACK.readback(TOKEN), {"leads": []})
        self.urlopen.side_effect = [response({"records": [], "offset": str(i)}) for i in range(9)] + [response({"records": []})]
        self.assertEqual(READBACK.readback(TOKEN), {"leads": []})
        self.assertEqual(self.urlopen.call_count, 11)

    def test_pagination_is_bounded_and_never_returns_partial_output(self):
        self.urlopen.side_effect = [response({"records": [], "offset": "more"}) for _ in range(10)]
        self.assertEqual(self.invoke(), 1)
        self.assertEqual(self.urlopen.call_count, 10)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.stderr.getvalue(), "ERROR: read-back failed\n")

    def test_malformed_responses_refused(self):
        bad = [None, [], {}, {"records": None}, {"records": {}}, {"records": [None]},
               {"records": [{}]}, {"records": [{"fields": []}]},
               {"records": [{"fields": {}}]}, {"records": [{"fields": {"Email": ""}}]},
               {"records": [{"fields": {"Email": 1}}]}]
        bad += [{"records": [], "offset": value} for value in (None, "", 4, [], {})]
        for payload in bad:
            with self.subTest(payload=payload):
                self.urlopen.side_effect = [response(payload)]
                self.assertEqual(self.invoke(), 1)
                self.assertFalse(self.out.exists())
        self.urlopen.side_effect = [io.BytesIO(b'{bad')]
        self.assertEqual(self.invoke(), 1)

    def test_leak_forms_and_exception_text_never_reach_output(self):
        for value in (TOKEN, "Bearer " + TOKEN, b64encode(TOKEN.encode()).decode(),
                      quote(TOKEN, safe=""), quote_plus(TOKEN, safe="")):
            with self.subTest(form=value[:8]):
                self.urlopen.side_effect = [response({"records": [{"fields": {"Email": value}}]})]
                self.assertEqual(self.invoke(), 1)
                self.assertFalse(self.out.exists())
        self.urlopen.side_effect = OSError(TOKEN)
        self.assertEqual(self.invoke(), 1)
        self.assertNotIn(TOKEN, self.stderr.getvalue())
        self.assertNotIn(str(self.secret), self.stderr.getvalue())

    def test_owned_regular_0600_file_comments_and_bounds(self):
        self.assertEqual(READBACK.read_secret_file(self.secret), TOKEN)
        self.assertEqual(stat.S_IMODE(self.secret.stat().st_mode), 0o600)
        for prefix, suffix in (("a" * 8, "B" * 32), ("a" * 32, "Z" * 128)):
            token = "pat" + prefix + "." + suffix
            self.secret.write_text("AIRTABLE_TOKEN=" + token)
            self.assertEqual(READBACK.read_secret_file(self.secret), token)
        text = "AIRTABLE_TOKEN=" + TOKEN + "\n"
        self.secret.write_text(text + "#" + "x" * (65536 - len(text) - 1))
        self.assertEqual(READBACK.read_secret_file(self.secret), TOKEN)
        self.secret.write_text(self.secret.read_text() + "x")
        self.assert_bad_secret()

    def test_permissions_owner_symlink_directory_fifo_and_missing(self):
        for mode in (0o400, 0o640, 0o644, 0o700):
            self.secret.chmod(mode)
            self.assert_bad_secret()
        self.secret.chmod(0o600)
        with mock.patch.object(READBACK.os, "getuid", return_value=os.getuid() + 1):
            self.assert_bad_secret()
        link = self.root / "link"
        link.symlink_to(self.secret)
        self.assert_bad_secret(link)
        self.assert_bad_secret(self.root)
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        self.assert_bad_secret(fifo)
        self.assert_bad_secret(self.root / "missing")

    def test_invalid_secret_syntax_and_token_shapes(self):
        texts = ["", "# only comments\n", "bad line", "AIRTABLE_TOKEN=", "OTHER=" + TOKEN,
                 "AIRTABLE_TOKEN=" + TOKEN + "\nAIRTABLE_TOKEN=" + TOKEN,
                 "AIRTABLE_TOKEN=" + TOKEN + "\nOTHER=value", " AIRTABLE_TOKEN=" + TOKEN]
        values = [TOKEN + " ", " " + TOKEN, TOKEN + "\t", TOKEN + "\x00", "wrong",
                  "pat" + "a" * 7 + "." + "B" * 32, "pat" + "a" * 33 + "." + "B" * 32,
                  "patabcdefgh." + "B" * 31, "patabcdefgh." + "B" * 129,
                  "patabcdef_g." + "B" * 32, "patabcdefgh." + "B" * 31 + "_"]
        for text in texts + ["AIRTABLE_TOKEN=" + value for value in values]:
            self.secret.write_text(text)
            self.assert_bad_secret()
        self.secret.write_bytes(b"AIRTABLE_TOKEN=\xff")
        self.assert_bad_secret()

    def test_no_ambient_proxy_no_redirects_and_import_has_no_requests(self):
        self.assertIsNone(READBACK.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://wrong.test"))
        opener = mock.Mock()
        with mock.patch("urllib.request.ProxyHandler", wraps=READBACK.ProxyHandler) as proxy, \
                mock.patch("urllib.request.build_opener", return_value=opener) as build:
            imported = importlib.util.module_from_spec(SPEC)
            SPEC.loader.exec_module(imported)
        proxy.assert_called_once_with({})
        self.assertIsInstance(build.call_args.args[1], imported.NoRedirect)
        opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
