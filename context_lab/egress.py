"""Origin-pinned HTTP egress for model API calls."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def assert_local_host(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise ValueError("CONTEXT_LAB_LOCAL_ONLY requires a loopback CONTEXT_LAB_BASE_URL (127.0.0.1, localhost, ::1)")
    if parsed.username or parsed.password:
        raise ValueError("Model endpoint URL must not include userinfo")


def env_local_only() -> bool:
    raw = os.environ.get("CONTEXT_LAB_LOCAL_ONLY", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _effective_port(parsed: urllib.parse.ParseResult) -> int:
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int
    loopback: bool

    @classmethod
    def from_url(cls, url: str) -> Origin:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        return cls(
            scheme=(parsed.scheme or "").lower(),
            host=host,
            port=_effective_port(parsed),
            loopback=host in LOOPBACK_HOSTS,
        )

    def same_origin(self, url: str) -> bool:
        other = Origin.from_url(url)
        return self.scheme == other.scheme and self.host == other.host and self.port == other.port


class LoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave loopback when local_only is on."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_local_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OriginPinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave the pinned origin."""

    def __init__(self, origin: Origin):
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self._origin.same_origin(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class EgressClient:
    """Stdlib HTTP with origin-pinned redirects and credential policy."""

    def __init__(self, base_url: str, *, api_key: str = "", local_only: bool | None = None):
        self.base = base_url.rstrip("/")
        if not self.base.startswith(("http://", "https://")):
            raise ValueError("Set CONTEXT_LAB_BASE_URL to a compatible endpoint, including /v1 if required")
        if local_only is None:
            local_only = env_local_only()
        self.local_only = bool(local_only)
        self.origin = Origin.from_url(self.base)
        self.api_key = api_key
        if self.local_only:
            assert_local_host(self.base)
        elif not self.origin.loopback and self.origin.scheme == "http":
            raise ValueError("Remote model endpoints must use HTTPS when CONTEXT_LAB_LOCAL_ONLY is disabled")

    @classmethod
    def from_env(cls, *, local_only: bool | None = None) -> EgressClient:
        return cls(
            os.environ.get("CONTEXT_LAB_BASE_URL", ""),
            api_key=os.environ.get("CONTEXT_LAB_API_KEY", ""),
            local_only=local_only,
        )

    def _build_opener(self):
        if self.local_only:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                LoopbackRedirectHandler,
            )
        return urllib.request.build_opener(OriginPinnedRedirectHandler(self.origin))

    def post_json(self, route: str, payload: dict, *, timeout: float = 90) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.base + route, data=json.dumps(payload).encode(), headers=headers)
        try:
            with self._build_opener().open(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raise ValueError(f"Model endpoint returned HTTP {e.code}; check model, URL and credentials") from None
        except (urllib.error.URLError, TimeoutError):
            raise ValueError("Could not reach model endpoint") from None
