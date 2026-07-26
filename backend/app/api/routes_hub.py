"""Discovery, pairing and local Hub administration endpoints."""

from __future__ import annotations

from datetime import timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.hub.context import HubContext
from app.config import Settings, get_settings
from app.hub.identity import get_hub_identity_store

router = APIRouter(prefix="/hub", tags=["hub"])


class PairStartIn(BaseModel):
    client_name: str = Field(default="PaperFlow Web", max_length=120)
    workspace_id: str = Field(default="personal", min_length=1, max_length=80)


class PairConfirmIn(BaseModel):
    challenge_id: str = Field(min_length=8, max_length=200)
    code: str = Field(min_length=6, max_length=12)
    workspace_id: str = Field(default="personal", min_length=1, max_length=80)


def _request_origin(request: Request) -> str:
    return (request.headers.get("origin") or "local-native").rstrip("/")


def _requires_authorization(request: Request, settings: Settings) -> bool:
    if settings.hub_require_pairing:
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    origin = origin.rstrip("/")
    host = request.headers.get("host", "")
    try:
        if urlsplit(origin).netloc == host:
            return False
    except ValueError:
        return True
    return origin not in {value.rstrip("/") for value in settings.hub_trusted_unpaired_origins}


def _request_token(request: Request) -> str:
    token = request.headers.get("x-paperflow-hub-token", "")
    if token:
        return token
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


@router.get("/info")
def hub_info(request: Request) -> dict:
    settings = get_settings()
    identity = get_hub_identity_store()
    origin = _request_origin(request)
    workspace_id = request.headers.get("x-paperflow-workspace") or settings.hub_default_workspace_id
    authorization_required = _requires_authorization(request, settings)
    client = None
    if authorization_required:
        client = identity.verify_token(
            _request_token(request),
            origin=origin,
            workspace_id=workspace_id,
        )

    return {
        "product": "PaperFlow Hub",
        "protocolVersion": 1,
        "version": settings.version,
        "installationId": identity.installation_id,
        "deploymentMode": settings.hub_mode,
        "workspace": {
            "id": settings.hub_default_workspace_id,
            "scopeHeader": "X-PaperFlow-Workspace",
            "multiWorkspaceEnabled": settings.hub_mode == "school" and settings.hub_school_tenancy_enabled,
        },
        "authorization": {
            "required": authorization_required,
            "authorized": not authorization_required or client is not None,
            "tokenHeader": "X-PaperFlow-Hub-Token",
            "webSocketQueryParameter": "hub_token",
            "pairingSupported": True,
        },
        "capabilities": {
            "localProcessing": True,
            "localStorage": True,
            "cameraStreaming": True,
            "ocr": True,
            "backup": True,
            "schoolModeFoundation": True,
            "schoolModeEnabled": settings.hub_mode == "school" and settings.hub_school_tenancy_enabled,
        },
        "privacy": {
            "studentDataLocation": "local-hub",
            "cloudStudentDataTransfer": False,
            "cloudControlPlane": "metadata-only",
        },
    }


@router.post("/pair/start")
def start_pairing(payload: PairStartIn, request: Request) -> dict:
    settings = get_settings()
    if settings.hub_mode == "personal" and payload.workspace_id != settings.hub_default_workspace_id:
        raise HTTPException(status_code=400, detail="Неверное рабочее пространство персонального Hub")

    challenge = get_hub_identity_store().start_pairing(
        origin=_request_origin(request),
        client_name=payload.client_name,
        workspace_id=payload.workspace_id,
    )
    response = {
        "challengeId": challenge.id,
        "expiresAt": challenge.expires_at.astimezone(timezone.utc).isoformat(),
        "codeLength": 6,
        "displayRequired": True,
        "displayUrl": f"{settings.hub_public_url.rstrip('/')}/api/hub/pair/display/{challenge.id}",
    }
    if settings.hub_pairing_dev_echo_code:
        response["devCode"] = challenge.code
    return response


@router.get("/pair/display/{challenge_id}", response_class=HTMLResponse)
def display_pairing_code(challenge_id: str) -> HTMLResponse:
    """Local confirmation page opened by the teacher in a separate tab."""
    code = get_hub_identity_store().pending_pairing_code(challenge_id)
    if code is None:
        raise HTTPException(status_code=404, detail="Код подключения истёк или не существует")
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="referrer" content="no-referrer">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Подключение PaperFlow Hub</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh; display: grid;
            place-items: center; background: #f4f6f8; color: #15202b; }}
    main {{ width: min(520px, calc(100% - 40px)); background: white; border-radius: 20px;
            padding: 36px; box-shadow: 0 20px 60px rgba(20, 32, 43, .12); text-align: center; }}
    .code {{ font: 700 48px/1.1 ui-monospace, monospace; letter-spacing: .18em; margin: 24px 0; }}
    p {{ color: #5f6b76; line-height: 1.55; }}
  </style>
</head>
<body>
  <main>
    <h1>Подключение к PaperFlow Hub</h1>
    <p>Введите этот код в окне PaperFlow Web. Код действует ограниченное время.</p>
    <div class="code">{code}</div>
    <p>Не передавайте код другим людям. После подключения эту вкладку можно закрыть.</p>
  </main>
</body>
</html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.post("/pair/confirm")
def confirm_pairing(payload: PairConfirmIn, request: Request) -> dict:
    try:
        token, client = get_hub_identity_store().confirm_pairing(
            challenge_id=payload.challenge_id,
            code=payload.code,
            origin=_request_origin(request),
            workspace_id=payload.workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "token": token,
        "tokenType": "PaperFlowHub",
        "client": client.public_dict(),
    }


@router.get("/context")
def current_context(context: HubContext) -> dict:
    return {
        "installationId": context.installation_id,
        "deploymentMode": context.deployment_mode,
        "workspaceId": context.workspace_id,
        "actorId": context.actor_id,
        "role": context.role,
        "clientId": context.client_id,
        "authenticated": context.authenticated,
    }


@router.get("/clients")
def list_clients(context: HubContext) -> dict:
    if context.role not in {"owner", "admin", "local"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав для просмотра подключений")
    return {"clients": get_hub_identity_store().list_clients()}


@router.delete("/clients/{client_id}", status_code=204)
def revoke_client(client_id: str, context: HubContext) -> None:
    if context.role not in {"owner", "admin", "local"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав для отключения клиента")
    if not get_hub_identity_store().revoke_client(client_id):
        raise HTTPException(status_code=404, detail="Подключение не найдено")
