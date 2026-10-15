"""Unit tests for the solver's proxy URL handling.

Chromium reads proxy credentials only from the dedicated ``username``/``password``
launch options, and cannot authenticate to an upstream SOCKS5 proxy at all, so
these helpers decide what the browser is actually launched with.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOLVER_DIR = Path(__file__).resolve().parents[1] / "sentinel_solver"
if str(SOLVER_DIR) not in sys.path:
    sys.path.insert(0, str(SOLVER_DIR))

from proxy_options import (  # noqa: E402
    LOCAL_FORWARD_SERVER,
    browser_proxy_options,
    needs_local_proxy,
    proxy_options_from_url,
)


class ProxyOptionsFromUrlTests(unittest.TestCase):
    def test_empty_values_return_none(self):
        for value in ("", "   ", None):
            self.assertIsNone(proxy_options_from_url(value))

    def test_plain_http_proxy_keeps_server_only(self):
        self.assertEqual(proxy_options_from_url("http://privoxy:8118"), {"server": "http://privoxy:8118"})

    def test_credentials_move_out_of_server(self):
        self.assertEqual(
            proxy_options_from_url("http://warp:secret@158.101.0.212:40080"),
            {"server": "http://158.101.0.212:40080", "username": "warp", "password": "secret"},
        )

    def test_percent_encoded_credentials_are_decoded(self):
        options = proxy_options_from_url("http://u:p%40ss%2Fword@host:8080")
        self.assertEqual(options["server"], "http://host:8080")
        self.assertEqual(options["username"], "u")
        self.assertEqual(options["password"], "p@ss/word")

    def test_username_without_password(self):
        self.assertEqual(proxy_options_from_url("http://u@host:8080"), {"server": "http://host:8080", "username": "u"})

    def test_socks_url_keeps_credentials(self):
        self.assertEqual(
            proxy_options_from_url("socks5://user:pass@127.0.0.1:1080"),
            {"server": "socks5://127.0.0.1:1080", "username": "user", "password": "pass"},
        )

    def test_bad_port_falls_back_to_raw_value(self):
        self.assertEqual(proxy_options_from_url("http://host:not-a-port"), {"server": "http://host:not-a-port"})

    def test_missing_scheme_falls_back_to_raw_value(self):
        self.assertEqual(proxy_options_from_url("host:8080"), {"server": "host:8080"})


class NeedsLocalProxyTests(unittest.TestCase):
    def test_missing_options(self):
        self.assertFalse(needs_local_proxy(None))
        self.assertFalse(needs_local_proxy({}))

    def test_authenticated_socks_needs_forwarding(self):
        self.assertTrue(needs_local_proxy(proxy_options_from_url("socks5://u:p@host:1080")))
        self.assertTrue(needs_local_proxy(proxy_options_from_url("socks4://u:p@host:1080")))

    def test_anonymous_socks_is_usable(self):
        self.assertFalse(needs_local_proxy(proxy_options_from_url("socks5://host:1080")))

    def test_authenticated_http_is_usable(self):
        self.assertFalse(needs_local_proxy(proxy_options_from_url("http://u:p@host:8118")))


class BrowserProxyOptionsTests(unittest.TestCase):
    def test_upstream_wins_and_uses_local_forward(self):
        options, note = browser_proxy_options("http://u:p@host:8118", "socks5://u:p@host:1080")
        self.assertEqual(options, {"server": LOCAL_FORWARD_SERVER})
        self.assertIn("forwarded", note)

    def test_plain_proxy_has_no_note(self):
        options, note = browser_proxy_options("http://privoxy:8118", "")
        self.assertEqual(options, {"server": "http://privoxy:8118"})
        self.assertEqual(note, "")

    def test_authenticated_http_gets_credentials(self):
        options, note = browser_proxy_options("http://warp:pw@host:40080", "")
        self.assertEqual(options, {"server": "http://host:40080", "username": "warp", "password": "pw"})
        self.assertEqual(note, "")

    def test_authenticated_socks_without_upstream_is_reported(self):
        options, note = browser_proxy_options("socks5://u:p@host:1080", "")
        self.assertEqual(options["server"], "socks5://host:1080")
        self.assertIn("SENTINEL_SOLVER_UPSTREAM", note)

    def test_no_proxy_at_all(self):
        options, note = browser_proxy_options("", "")
        self.assertIsNone(options)
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
