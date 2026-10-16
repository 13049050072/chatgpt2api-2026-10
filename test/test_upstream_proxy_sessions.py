"""OpenAI-upstream sessions have to share the configured egress.

The proxy runtime is scoped to "upstream" traffic on purpose (see
``test_proxy_service.test_runtime_proxy_is_limited_to_upstream_scope_by_default``),
so a call site that talks to chatgpt.com / auth.openai.com but forgets
``upstream=True`` silently keeps using the direct egress. That split is what makes
a harvested cf_clearance useless: the cookie is bound to the exit IP that solved
the challenge, so the bootstrap request is answered with a Cloudflare challenge
instead of the app shell.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.proxy_service import proxy_settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules whose requests target the OpenAI / ChatGPT upstream.
UPSTREAM_MODULES = (
    "services/openai_backend_api.py",
    "services/account_service.py",
    "services/oauth_login_service.py",
)


def _call_arguments(text: str, needle: str) -> list[str]:
    """Return the argument text of every balanced ``needle(...)`` call."""
    found: list[str] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return found
        depth = 0
        cursor = index + len(needle) - 1
        while cursor < len(text):
            char = text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        found.append(text[index + len(needle) : cursor])
        start = cursor + 1


class UpstreamProxySessionTests(unittest.TestCase):
    def test_backend_session_asks_for_the_upstream_proxy(self) -> None:
        with mock.patch.object(proxy_settings, "build_session_kwargs", return_value={}) as builder:
            api = OpenAIBackendAPI("")
            self.addCleanup(api.close)
        self.assertEqual(builder.call_count, 1)
        self.assertTrue(
            builder.call_args.kwargs.get("upstream"),
            "the ChatGPT backend session must be built for upstream scope",
        )

    def test_openai_upstream_call_sites_pass_the_upstream_flag(self) -> None:
        for relative in UPSTREAM_MODULES:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            calls = _call_arguments(source, "build_session_kwargs(")
            self.assertTrue(calls, f"{relative} should build at least one session")
            for arguments in calls:
                self.assertIn(
                    "upstream=True",
                    arguments,
                    f"{relative}: build_session_kwargs({arguments.strip()}) must pass upstream=True",
                )

    def test_non_upstream_call_sites_are_not_pinned_to_the_runtime_proxy(self) -> None:
        # Fetching a user supplied image URL must not be forced through the OpenAI egress.
        source = (REPO_ROOT / "utils/helper.py").read_text(encoding="utf-8")
        for arguments in _call_arguments(source, "build_session_kwargs("):
            self.assertNotIn("upstream=True", arguments)


if __name__ == "__main__":
    unittest.main()
