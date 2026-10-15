"""Proxy URL handling for the solver's browser launcher.

Chromium only reads proxy credentials from the dedicated ``username``/``password``
launch options. A ``user:pass@host`` userinfo section left inside ``server`` is
ignored, so the browser tries the proxy anonymously and dies with
``net::ERR_INVALID_AUTH_CREDENTIALS``. Chromium additionally cannot authenticate
against an upstream SOCKS5 proxy at all; those upstreams have to be forwarded by
``entrypoint.sh`` (``SENTINEL_SOLVER_UPSTREAM``) to an auth-free local socks5 that
the browser can talk to.
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit

#: Where entrypoint.sh exposes the forwarded (auth-free) upstream.
LOCAL_FORWARD_SERVER = "socks5://127.0.0.1:1080"

SOCKS_SCHEMES = ("socks", "socks4", "socks4a", "socks5", "socks5h")


def proxy_options_from_url(raw: str) -> dict[str, str] | None:
    """Split a proxy URL into playwright's ``{server, username, password}``.

    Anything that cannot be parsed is handed to the browser verbatim so the
    failure stays visible instead of silently dropping the proxy.
    """
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return {"server": value}
    if not parts.scheme or not host:
        return {"server": value}
    options = {"server": f"{parts.scheme}://{host}" + (f":{port}" if port else "")}
    if parts.username:
        options["username"] = unquote(parts.username)
    if parts.password:
        options["password"] = unquote(parts.password)
    return options


def needs_local_proxy(options: dict[str, str] | None) -> bool:
    """True when the browser cannot use this upstream directly (authenticated SOCKS)."""
    if not options:
        return False
    scheme = str(options.get("server") or "").split("://", 1)[0].strip().lower()
    if scheme not in SOCKS_SCHEMES:
        return False
    return bool(options.get("username") or options.get("password"))


def browser_proxy_options(
    proxy_url: str,
    upstream: str,
    local_server: str = LOCAL_FORWARD_SERVER,
) -> tuple[dict[str, str] | None, str]:
    """Resolve the launch proxy for the solver browser.

    Returns the playwright options plus a human-readable note for the log.
    ``SENTINEL_SOLVER_UPSTREAM`` wins: entrypoint.sh has already forwarded that
    authenticated upstream to an auth-free local socks5, so pointing the browser
    straight at it keeps the solver working with any upstream.
    """
    if str(upstream or "").strip():
        return {"server": local_server}, f"using forwarded upstream on {local_server}"
    options = proxy_options_from_url(proxy_url)
    if needs_local_proxy(options):
        return (
            options,
            "authenticated SOCKS upstream: Chromium cannot authenticate to it; "
            "set SENTINEL_SOLVER_UPSTREAM so entrypoint.sh forwards it locally",
        )
    return options, ""
