"""Typed daemon Adapter for the QQ owner's private Dashboard snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from .dashboard_home_snapshot import DashboardHomeSnapshot


_SNAPSHOT_PATH = "/internal/world-v2/dashboard/operator-snapshot"


class DashboardHomeSourceError(RuntimeError):
    """Stable, secret-free failure returned by the remote owner Adapter."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"dashboard owner source failed: {code}")


@dataclass(frozen=True, slots=True)
class DashboardHomeFetchResult:
    snapshot: DashboardHomeSnapshot | None
    etag: str
    not_modified: bool

    def __post_init__(self) -> None:
        if not self.etag:
            raise ValueError("dashboard owner response requires an ETag")
        if self.not_modified != (self.snapshot is None):
            raise ValueError("dashboard owner response state is inconsistent")


class DashboardHomeSource(Protocol):
    async def fetch(
        self,
        *,
        if_none_match: str | None = None,
    ) -> DashboardHomeFetchResult: ...


class QQDashboardHomeHttpAdapter:
    """Read one fixed QQ-owner route without inheriting ambient proxy state."""

    def __init__(
        self,
        *,
        base_url: str,
        operator_token: str,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not operator_token.strip():
            raise ValueError("dashboard owner Adapter requires its read-only token")
        if timeout_seconds <= 0:
            raise ValueError("dashboard owner Adapter timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("dashboard owner response limit must be positive")
        self._url = _snapshot_url(base_url)
        self._operator_token = operator_token.strip()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    async def fetch(
        self,
        *,
        if_none_match: str | None = None,
    ) -> DashboardHomeFetchResult:
        headers = {
            "Accept": "application/json",
            "X-World-V2-Internal-Token": self._operator_token,
        }
        if if_none_match is not None:
            headers["If-None-Match"] = if_none_match
        body = bytearray()
        etag: str | None = None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", self._url, headers=headers) as response:
                    etag = response.headers.get("ETag")
                    if response.status_code == 304:
                        if etag is None or if_none_match is None or etag != if_none_match:
                            raise DashboardHomeSourceError("invalid_not_modified")
                        return DashboardHomeFetchResult(
                            snapshot=None,
                            etag=etag,
                            not_modified=True,
                        )
                    if 300 <= response.status_code < 400:
                        raise DashboardHomeSourceError("redirect_rejected")
                    if response.status_code == 403:
                        raise DashboardHomeSourceError("owner_auth_misconfigured")
                    if response.status_code == 503:
                        raise DashboardHomeSourceError("owner_unavailable")
                    if response.status_code != 200:
                        raise DashboardHomeSourceError("unexpected_owner_status")
                    media_type = (
                        response.headers.get("Content-Type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if media_type != "application/json":
                        raise DashboardHomeSourceError("invalid_content_type")
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self._max_response_bytes:
                            raise DashboardHomeSourceError("response_too_large")
                        body.extend(chunk)
        except httpx.TransportError as exc:
            raise DashboardHomeSourceError("owner_unreachable") from exc

        try:
            snapshot = DashboardHomeSnapshot.model_validate_json(
                bytes(body),
                strict=True,
            )
        except ValueError as exc:
            raise DashboardHomeSourceError("invalid_owner_contract") from exc
        expected_etag = f'"{snapshot.snapshot_hash}"'
        if etag != expected_etag:
            raise DashboardHomeSourceError("etag_mismatch")
        return DashboardHomeFetchResult(
            snapshot=snapshot,
            etag=etag,
            not_modified=False,
        )


def _snapshot_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("QQ dashboard owner URL must be HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("QQ dashboard owner URL must not contain userinfo")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("QQ dashboard owner URL must be an origin without path or query")
    if not _is_loopback(parsed.hostname):
        raise ValueError("QQ dashboard owner URL must be loopback")
    return base_url.rstrip("/") + _SNAPSHOT_PATH


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


__all__ = [
    "DashboardHomeFetchResult",
    "DashboardHomeSource",
    "DashboardHomeSourceError",
    "QQDashboardHomeHttpAdapter",
]
