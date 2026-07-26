"""ASGI security boundary between the cloud UI and the local Hub."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from starlette.datastructures import Headers, QueryParams

from app.config import Settings
from app.hub.context import HubRequestContext
from app.hub.identity import HubIdentityStore


_PUBLIC_PATHS = {
    "/api/health",
    "/api/hub/info",
    "/api/hub/pair/start",
    "/api/hub/pair/confirm",
}
_WEBSOCKET_TOKEN_PREFIX = "paperflow-auth."


def _normalise_origin(origin: str) -> str:
    return origin.rstrip("/")


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_secure_bootstrap_origin(origin: str) -> bool:
    """Allow discovery/pairing from HTTPS or a local development origin only."""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and _is_loopback_host(parsed.hostname)


def _bearer_token(headers: Headers) -> str:
    explicit = headers.get("x-paperflow-hub-token", "")
    if explicit:
        return explicit
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _websocket_protocol_token(headers: Headers) -> str:
    """Read the token from a WebSocket subprotocol, not from the logged URL."""
    raw = headers.get("sec-websocket-protocol", "")
    for protocol in (value.strip() for value in raw.split(",")):
        if protocol.startswith(_WEBSOCKET_TOKEN_PREFIX):
            return protocol[len(_WEBSOCKET_TOKEN_PREFIX) :]
    return ""


class HubSecurityMiddleware:
    """Validate Origin, workspace and local pairing token for API traffic."""

    def __init__(self, app, *, settings: Settings, identity: HubIdentityStore) -> None:
        self.app = app
        self.settings = settings
        self.identity = identity
        self.allowed_origins = {
            _normalise_origin(value)
            for value in settings.all_cors_origins
            if value and value != "*"
        }
        self.trusted_unpaired_origins = {
            _normalise_origin(value)
            for value in settings.hub_trusted_unpaired_origins
            if value
        }

    def _origin_allowed(
        self,
        origin: str | None,
        headers: Headers,
        *,
        path: str,
        workspace_id: str,
    ) -> bool:
        if not origin:
            return True
        origin = _normalise_origin(origin)
        if "*" in self.settings.all_cors_origins or origin in self.allowed_origins:
            return True
        host = headers.get("host", "")
        try:
            if urlsplit(origin).netloc == host:
                return True
        except ValueError:
            return False

        # A new deployment (Vercel, custom domain, future School portal) may
        # discover the Hub and start pairing without being preconfigured. The
        # code is still displayed only on a local top-level page, and no private
        # endpoint is opened until the exact Origin is persisted as a client.
        if path in _PUBLIC_PATHS and _is_secure_bootstrap_origin(origin):
            return True

        return self.identity.has_paired_origin(origin=origin, workspace_id=workspace_id)

    def _requires_auth(self, origin: str | None, headers: Headers) -> bool:
        if self.settings.hub_require_pairing:
            return True
        if not origin:
            return False
        origin = _normalise_origin(origin)
        host = headers.get("host", "")
        try:
            if urlsplit(origin).netloc == host:
                return False
        except ValueError:
            return True
        return origin not in self.trusted_unpaired_origins

    @staticmethod
    def _query_token(scope) -> str:
        query = QueryParams(scope.get("query_string", b"").decode("latin-1"))
        return query.get("hub_token", "")

    @staticmethod
    async def _reject(scope, send, status_code: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401 if status_code == 401 else 4403})
            return
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin:
            origin = _normalise_origin(origin)

        query = QueryParams(scope.get("query_string", b"").decode("latin-1"))
        workspace_id = (
            headers.get("x-paperflow-workspace")
            or query.get("workspace")
            or self.settings.hub_default_workspace_id
        )
        if self.settings.hub_mode == "personal" and workspace_id != self.settings.hub_default_workspace_id:
            await self._reject(scope, send, 403, "Персональный Hub поддерживает только личное рабочее пространство")
            return

        if not self._origin_allowed(origin, headers, path=path, workspace_id=workspace_id):
            await self._reject(scope, send, 403, "Источник запроса не разрешён для этого PaperFlow Hub")
            return

        is_pairing_display = path.startswith("/api/hub/pair/display/")
        is_media_request = path.startswith("/api/sheets/") and "/image/" in path
        fetch_site = headers.get("sec-fetch-site", "").lower()
        cross_site_without_origin = not origin and fetch_site == "cross-site"
        if cross_site_without_origin and not is_pairing_display and not is_media_request:
            await self._reject(scope, send, 403, "Кросс-сайтовый браузерный запрос без Origin запрещён")
            return

        # The one-time code may only be viewed by top-level local navigation.
        # A CORS fetch from the cloud UI must never be able to read it.
        if is_pairing_display and origin:
            await self._reject(scope, send, 403, "Код подключения можно открыть только как локальную страницу")
            return

        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # A cross-site <img> request normally has no Origin header. It is allowed
        # only when the read-only media token validates successfully below.
        requires_auth = self._requires_auth(origin, headers) or (cross_site_without_origin and is_media_request)
        client = None
        is_public_path = path in _PUBLIC_PATHS or is_pairing_display
        if not is_public_path and requires_auth:
            if is_media_request:
                client = self.identity.verify_media_token(
                    self._query_token(scope),
                    origin=origin,
                    workspace_id=workspace_id,
                )
            else:
                token = _websocket_protocol_token(headers) if scope["type"] == "websocket" else _bearer_token(headers)
                client = self.identity.verify_token(
                    token,
                    origin=origin or "local-native",
                    workspace_id=workspace_id,
                )
            if client is None:
                await self._reject(scope, send, 401, "Требуется сопряжение с PaperFlow Hub")
                return

        if client is None:
            context = HubRequestContext(
                installation_id=self.identity.installation_id,
                deployment_mode=self.settings.hub_mode,
                workspace_id=workspace_id,
                actor_id="local-owner",
                role="local",
                client_id=None,
                origin=origin,
                authenticated=not requires_auth,
            )
        else:
            context = HubRequestContext(
                installation_id=self.identity.installation_id,
                deployment_mode=self.settings.hub_mode,
                workspace_id=client.workspace_id,
                actor_id=client.actor_id,
                role=client.role,  # type: ignore[arg-type]
                client_id=client.id,
                origin=origin,
                authenticated=True,
            )

        scope.setdefault("state", {})["hub_context"] = context
        await self.app(scope, receive, send)


class PrivateNetworkAccessMiddleware:
    """Add the browser PNA opt-in header for cloud-to-local preflights."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        requested = headers.get("access-control-request-private-network", "").lower() == "true"

        async def send_wrapper(message) -> None:
            if requested and message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append((b"access-control-allow-private-network", b"true"))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
