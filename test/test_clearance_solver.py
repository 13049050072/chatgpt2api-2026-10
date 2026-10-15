"""Cloudflare clearance regressions: the harvested cookie has to reach the wire.

A clearance bundle (cookie + User-Agent) is only accepted upstream when the
request actually sends both, and the User-Agent has to be the one that solved the
challenge. The store used to keep the bundle to itself, so these tests pin the
wiring down from the response of the solver service all the way to the headers of
the bootstrap request.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from curl_cffi.requests import Headers

from services.config import DEFAULT_PROXY_RUNTIME
from services.openai_backend_api import OpenAIBackendAPI
from services.proxy_service import (
    ClearanceBundle,
    FlareSolverrClearanceProvider,
    ProxySettingsStore,
    SentinelSolverClearanceProvider,
    client_hints_for_user_agent,
    proxy_settings,
)

LINUX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def runtime_with_clearance(mode: str, *, enabled: bool = True, proxy_url: str = "") -> dict:
    runtime = json.loads(json.dumps(DEFAULT_PROXY_RUNTIME))
    runtime["enabled"] = enabled
    runtime["egress_mode"] = "single_proxy" if enabled else "direct"
    runtime["proxy_url"] = proxy_url
    runtime["clearance"]["enabled"] = True
    runtime["clearance"]["mode"] = mode
    return runtime


class FakeConfig:
    def __init__(self, runtime: dict | None = None, solver_url: str = "", solver_key: str = "") -> None:
        self._runtime = runtime if runtime is not None else json.loads(json.dumps(DEFAULT_PROXY_RUNTIME))
        self.sentinel_solver_url = solver_url
        self.sentinel_solver_key = solver_key

    def get_proxy_settings(self) -> str:
        return ""

    def get_proxy_runtime_settings(self) -> dict:
        return json.loads(json.dumps(self._runtime))


class SentinelSolverClearanceProviderTests(unittest.TestCase):
    def test_parses_the_solver_response_into_a_bundle(self):
        calls: list[tuple[str, dict, float]] = []

        def request(endpoint: str, headers: dict, timeout: float) -> bytes:
            calls.append((endpoint, headers, timeout))
            return json.dumps(
                {
                    "ready": True,
                    "user_agent": LINUX_UA,
                    "cookies": [
                        {"name": "cf_clearance", "value": "abc", "domain": ".chatgpt.com", "path": "/"},
                        {"name": "__cf_bm", "value": "def", "domain": ".chatgpt.com", "path": "/"},
                        {"name": "", "value": "ignored"},
                    ],
                }
            ).encode("utf-8")

        provider = SentinelSolverClearanceProvider("http://solver:8080/", "solver-key", request)
        bundle = provider.get_clearance("https://chatgpt.com/backend-api/conversation")

        self.assertIsNotNone(bundle)
        self.assertEqual(calls[0][0], "http://solver:8080/v1/clearance")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer solver-key")
        self.assertEqual(bundle.user_agent, LINUX_UA)
        self.assertEqual(bundle.cookies, {"cf_clearance": "abc", "__cf_bm": "def"})
        self.assertEqual(bundle.target_host, "chatgpt.com")

    def test_failures_and_missing_urls_return_none(self):
        def boom(endpoint: str, headers: dict, timeout: float) -> bytes:
            raise OSError("solver down")

        self.assertIsNone(SentinelSolverClearanceProvider("", "k").get_clearance("https://chatgpt.com/"))
        self.assertIsNone(SentinelSolverClearanceProvider("http://solver:8080", "k", boom).get_clearance("https://chatgpt.com/"))


class ClearanceModeTests(unittest.TestCase):
    def test_solver_mode_clears_without_a_proxy_runtime(self):
        store = ProxySettingsStore(FakeConfig(runtime_with_clearance("solver", enabled=False), solver_url="http://solver:8080"))
        profile = store.get_profile(upstream=True)
        self.assertFalse(profile.runtime_enabled)
        self.assertTrue(profile.clearance_enabled)

    def test_store_builds_a_solver_provider_from_the_config(self):
        store = ProxySettingsStore(
            FakeConfig(runtime_with_clearance("solver"), solver_url="http://solver:8080", solver_key="k1")
        )
        profile = store.get_profile(upstream=True)
        provider = store._provider_for(profile)
        self.assertIsInstance(provider, SentinelSolverClearanceProvider)
        self.assertEqual(provider.base_url, "http://solver:8080")
        self.assertEqual(provider.key, "k1")

    def test_flaresolverr_mode_still_uses_the_flaresolverr_provider(self):
        runtime = runtime_with_clearance("flaresolverr")
        runtime["clearance"]["flaresolverr_url"] = "http://flaresolverr:8191"
        store = ProxySettingsStore(FakeConfig(runtime))
        profile = store.get_profile(upstream=True)
        self.assertIsInstance(store._provider_for(profile), FlareSolverrClearanceProvider)

    def test_missing_solver_url_disables_the_provider(self):
        store = ProxySettingsStore(FakeConfig(runtime_with_clearance("solver"), solver_url=""))
        profile = store.get_profile(upstream=True)
        self.assertIsNone(store._provider_for(profile))


class ClearanceHeaderTests(unittest.TestCase):
    def store_with_bundle(self, bundle: ClearanceBundle, proxy_url: str = "") -> ProxySettingsStore:
        modal = "single_proxy" if proxy_url else "direct"
        store = ProxySettingsStore(FakeConfig(runtime_with_clearance("solver", enabled=bool(proxy_url), proxy_url=proxy_url), solver_url="http://solver:8080"))
        store._set_cached_bundle(store._cache_key(proxy_url, "chatgpt.com"), bundle)
        self.assertEqual(store.get_profile(upstream=True).egress_mode, modal)
        return store

    def test_forced_user_agent_keeps_the_client_hints_consistent(self):
        bundle = ClearanceBundle(target_host="chatgpt.com", cookies={"cf_clearance": "abc"}, user_agent=LINUX_UA)
        store = self.store_with_bundle(bundle)
        headers = store.build_headers(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
                "Sec-Ch-Ua": '"Chromium";v="146", "Google Chrome";v="146"',
            },
            target_url="https://chatgpt.com/backend-api/me",
            force_user_agent=True,
        )
        self.assertEqual(headers["User-Agent"], LINUX_UA)
        self.assertEqual(headers["Sec-Ch-Ua-Platform"], '"Linux"')
        self.assertIn('"Google Chrome";v="148"', str(headers["Sec-Ch-Ua"]))
        self.assertIn("cf_clearance=abc", str(headers["Cookie"]))

    def test_wrong_target_host_is_not_reused(self):
        bundle = ClearanceBundle(target_host="chatgpt.com", cookies={"cf_clearance": "abc"}, user_agent=LINUX_UA)
        store = self.store_with_bundle(bundle)
        headers = store.build_headers(
            headers={"User-Agent": "agent"}, target_url="https://example.com/", force_user_agent=True
        )
        self.assertEqual(headers, {"User-Agent": "agent"})


class ClientHintTests(unittest.TestCase):
    def test_hints_follow_the_user_agent(self):
        linux = client_hints_for_user_agent(LINUX_UA)
        self.assertIn('"Chromium";v="148"', linux["Sec-Ch-Ua"])
        self.assertEqual(linux["Sec-Ch-Ua-Full-Version"], '"148.0.0.0"')
        self.assertEqual(linux["Sec-Ch-Ua-Platform"], '"Linux"')

        windows = client_hints_for_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36")
        self.assertEqual(windows["Sec-Ch-Ua-Platform"], '"Windows"')
        self.assertIn('"Google Chrome";v="146"', windows["Sec-Ch-Ua"])


class BootstrapClearanceTests(unittest.TestCase):
    def setUp(self):
        self.api = OpenAIBackendAPI("")
        self.addCleanup(self.api.close)

    def response(self, status: int = 200, headers: dict | None = None, text: str = ""):
        response = mock.Mock(status_code=status, headers=Headers(headers or {}), text=text)
        response.json.return_value = {}
        return response

    def clearance_build_headers(self):
        def build(headers, **kwargs):
            if not kwargs.get("force_user_agent"):
                return headers
            return {**headers, "User-Agent": LINUX_UA, "Cookie": "cf_clearance=abc"}

        return mock.patch.object(proxy_settings, "build_headers", side_effect=build)

    def test_bootstrap_retries_once_with_the_clearance_after_a_challenge(self):
        challenge = self.response(403, {"Content-Type": "text/html"}, "<html>challenge</html>")
        ok = self.response(200, {"Content-Type": "text/html"}, "<html>chatgpt</html>")
        bundle = ClearanceBundle(target_host="chatgpt.com", cookies={"cf_clearance": "abc"}, user_agent=LINUX_UA)
        with (
            mock.patch.object(self.api.session, "get", side_effect=[challenge, ok]) as get,
            mock.patch.object(proxy_settings, "refresh_clearance", return_value=bundle) as refresh,
            self.clearance_build_headers(),
        ):
            self.api._bootstrap()

        self.assertEqual(get.call_count, 2)
        refresh.assert_called_once()
        self.assertEqual(self.api.user_agent, LINUX_UA)
        self.assertIn("cf_clearance=abc", str(get.call_args_list[1].kwargs["headers"]["Cookie"]))

    def test_bootstrap_does_not_retry_when_clearance_is_unavailable(self):
        challenge = self.response(403, {"Content-Type": "text/html"}, "<html>challenge</html>")
        with (
            mock.patch.object(self.api.session, "get", side_effect=[challenge]) as get,
            mock.patch.object(proxy_settings, "refresh_clearance", return_value=None),
        ):
            with self.assertRaises(Exception):
                self.api._bootstrap()

        self.assertEqual(get.call_count, 1)

    def test_api_requests_carry_the_clearance_headers(self):
        with self.clearance_build_headers():
            headers = self.api._headers("/backend-api/conversation")

        self.assertEqual(headers["Cookie"], "cf_clearance=abc")
        self.assertEqual(headers["User-Agent"], LINUX_UA)
        self.assertEqual(headers["X-OpenAI-Target-Route"], "/backend-api/conversation")

class ClearanceBackoffTests(unittest.TestCase):
    def failing_provider(self) -> SentinelSolverClearanceProvider:
        def boom(*_args, **_kwargs):
            raise OSError("solver down")

        return SentinelSolverClearanceProvider("http://solver:8080", "k", boom)

    def test_a_failed_provider_is_not_retried_on_every_request(self):
        store = ProxySettingsStore(FakeConfig(runtime_with_clearance("solver"), solver_url="http://solver:8080"))
        with mock.patch.object(store, "_get_solver_provider", return_value=self.failing_provider()):
            self.assertIsNone(store.refresh_clearance(target_url="https://chatgpt.com/", force=True))
        self.assertTrue(store._in_clearance_backoff("chatgpt.com"))

        # The cooldown must short-circuit before the provider is consulted again.
        with mock.patch.object(store, "_get_solver_provider", side_effect=AssertionError("provider called during backoff")):
            self.assertIsNone(store.refresh_clearance(target_url="https://chatgpt.com/", force=True))

        # The manual probe in the settings UI bypasses the cooldown on purpose.
        with mock.patch.object(store, "_get_solver_provider", return_value=self.failing_provider()):
            self.assertIsNone(
                store.refresh_clearance(target_url="https://chatgpt.com/", force=True, ignore_backoff=True)
            )

    def test_a_successful_solve_clears_the_backoff(self):
        store = ProxySettingsStore(FakeConfig(runtime_with_clearance("solver"), solver_url="http://solver:8080"))
        store._mark_clearance_failure("chatgpt.com")
        payload = json.dumps(
            {"user_agent": LINUX_UA, "cookies": [{"name": "cf_clearance", "value": "abc", "domain": ".chatgpt.com"}]}
        ).encode("utf-8")
        working = SentinelSolverClearanceProvider("http://solver:8080", "k", lambda *args, **kwargs: payload)

        with mock.patch.object(store, "_get_solver_provider", return_value=working):
            bundle = store.refresh_clearance(target_url="https://chatgpt.com/", force=True, ignore_backoff=True)

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.cookies["cf_clearance"], "abc")
        self.assertFalse(store._in_clearance_backoff("chatgpt.com"))
