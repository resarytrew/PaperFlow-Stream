"""Security and compatibility tests for the Hybrid Local Hub foundation."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cloud.contracts import CloudTechnicalEvent, CloudUpdateCheck
from app.config import Settings
from app.hub.identity import HubIdentityStore
from app.middleware.hub_security import HubSecurityMiddleware, PrivateNetworkAccessMiddleware


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path,
        "hub_allowed_origins": ["https://web.paperflow.example"],
        "hub_trusted_unpaired_origins": [],
        "hub_public_url": "https://127.0.0.1:17841",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_pairing_tokens_are_hashed_and_bound_to_origin_and_workspace(tmp_path):
    settings = _settings(tmp_path)
    store = HubIdentityStore(settings, tmp_path / "hub")

    challenge = store.start_pairing(
        origin="https://web.paperflow.example",
        client_name="Teacher browser",
        workspace_id="personal",
    )
    token, client = store.confirm_pairing(
        challenge_id=challenge.id,
        code=challenge.code,
        origin="https://web.paperflow.example",
        workspace_id="personal",
    )

    identity_file = (tmp_path / "hub" / "identity.json").read_text(encoding="utf-8")
    assert token not in identity_file
    assert "token_hash" in identity_file
    verified = store.verify_token(
        token,
        origin="https://web.paperflow.example",
        workspace_id="personal",
    )
    assert verified is not None
    assert verified.id == client.id
    assert store.verify_token(
        token,
        origin="https://evil.example",
        workspace_id="personal",
    ) is None
    assert store.verify_token(
        token,
        origin="https://web.paperflow.example",
        workspace_id="another-school",
    ) is None


def test_pairing_code_is_invalidated_after_five_failed_attempts(tmp_path):
    settings = _settings(tmp_path)
    store = HubIdentityStore(settings, tmp_path / "hub")
    challenge = store.start_pairing(
        origin="https://web.paperflow.example",
        client_name="Teacher browser",
        workspace_id="personal",
    )

    for _ in range(4):
        with pytest.raises(ValueError, match="Неверный код"):
            store.confirm_pairing(
                challenge_id=challenge.id,
                code="000000" if challenge.code != "000000" else "999999",
                origin="https://web.paperflow.example",
                workspace_id="personal",
            )

    with pytest.raises(ValueError, match="заблокирован"):
        store.confirm_pairing(
            challenge_id=challenge.id,
            code="000000" if challenge.code != "000000" else "999999",
            origin="https://web.paperflow.example",
            workspace_id="personal",
        )
    assert store.pending_pairing_code(challenge.id) is None


def test_security_middleware_requires_pairing_for_external_web_origin(tmp_path):
    settings = _settings(tmp_path)
    identity = HubIdentityStore(settings, tmp_path / "hub")
    app = FastAPI()
    app.add_middleware(HubSecurityMiddleware, settings=settings, identity=identity)
    app.add_middleware(PrivateNetworkAccessMiddleware)

    @app.get("/api/private")
    def private_endpoint(request: Request) -> dict:
        context = request.state.hub_context
        return {
            "workspace": context.workspace_id,
            "client": context.client_id,
            "actor": context.actor_id,
        }

    client = TestClient(app)
    origin_headers = {
        "Origin": "https://web.paperflow.example",
        "X-PaperFlow-Workspace": "personal",
    }

    assert client.get("/api/private", headers=origin_headers).status_code == 401
    assert client.get("/api/private", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get(
        "/api/hub/pair/display/challenge",
        headers={"Origin": "https://web.paperflow.example"},
    ).status_code == 403

    challenge = identity.start_pairing(
        origin="https://web.paperflow.example",
        client_name="Teacher browser",
        workspace_id="personal",
    )
    token, connected = identity.confirm_pairing(
        challenge_id=challenge.id,
        code=challenge.code,
        origin="https://web.paperflow.example",
        workspace_id="personal",
    )
    response = client.get(
        "/api/private",
        headers={**origin_headers, "X-PaperFlow-Hub-Token": token},
    )
    assert response.status_code == 200
    assert response.json() == {
        "workspace": "personal",
        "client": connected.id,
        "actor": "local-owner",
    }

    wrong_workspace = client.get(
        "/api/private",
        headers={
            "Origin": "https://web.paperflow.example",
            "X-PaperFlow-Workspace": "school-b",
            "X-PaperFlow-Hub-Token": token,
        },
    )
    assert wrong_workspace.status_code == 403


def test_websocket_uses_subprotocol_token_instead_of_query_string(tmp_path):
    settings = _settings(tmp_path)
    identity = HubIdentityStore(settings, tmp_path / "hub")
    app = FastAPI()
    app.add_middleware(HubSecurityMiddleware, settings=settings, identity=identity)

    @app.websocket("/api/ws/private")
    async def private_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        context = websocket.scope["state"]["hub_context"]
        await websocket.send_json({"client": context.client_id, "workspace": context.workspace_id})
        await websocket.close()

    challenge = identity.start_pairing(
        origin="https://web.paperflow.example",
        client_name="Teacher browser",
        workspace_id="personal",
    )
    token, connected = identity.confirm_pairing(
        challenge_id=challenge.id,
        code=challenge.code,
        origin="https://web.paperflow.example",
        workspace_id="personal",
    )

    with TestClient(app).websocket_connect(
        "/api/ws/private?workspace=personal",
        headers={"Origin": "https://web.paperflow.example"},
        subprotocols=["paperflow.v1", f"paperflow-auth.{token}"],
    ) as websocket:
        assert websocket.receive_json() == {"client": connected.id, "workspace": "personal"}


def test_private_network_preflight_header_is_added(tmp_path):
    settings = _settings(tmp_path)
    identity = HubIdentityStore(settings, tmp_path / "hub")
    app = FastAPI()
    app.add_middleware(HubSecurityMiddleware, settings=settings, identity=identity)
    app.add_middleware(PrivateNetworkAccessMiddleware)

    @app.get("/api/private")
    def private_endpoint() -> dict:
        return {"ok": True}

    response = TestClient(app).options(
        "/api/private",
        headers={
            "Origin": "https://web.paperflow.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert response.headers["access-control-allow-private-network"] == "true"


def test_public_hub_info_contains_only_architecture_metadata(api_client):
    response = api_client.get("/api/hub/info")
    assert response.status_code == 200
    body = response.json()
    assert body["product"] == "PaperFlow Hub"
    assert body["protocolVersion"] == 1
    assert body["privacy"]["cloudStudentDataTransfer"] is False
    assert body["workspace"]["id"] == "personal"
    assert "dataDir" not in body

    health = api_client.get("/api/health").json()
    assert "dataDir" not in health
    assert "history" not in json.dumps(health)
    assert "sheetId" not in json.dumps(health)
    assert health["product"] == "PaperFlow Hub"


def test_cloud_contract_has_no_free_form_student_payload():
    update = CloudUpdateCheck(
        installation_id="install-12345678",
        hub_version="0.3.0",
        platform="windows",
        architecture="x86_64",
    )
    assert "student" not in json.dumps(update.model_dump()).lower()

    with pytest.raises(ValidationError):
        CloudUpdateCheck(
            installation_id="install-12345678",
            hub_version="0.3.0",
            platform="windows",
            student_name="Иванов Иван",  # type: ignore[call-arg]
        )

    CloudTechnicalEvent(
        installation_id="install-12345678",
        event="hub_started",
        hub_version="0.3.0",
    )


def test_school_mode_cannot_be_enabled_before_tenant_schema(tmp_path):
    with pytest.raises(ValidationError):
        _settings(tmp_path, hub_mode="school", hub_school_tenancy_enabled=False)

    settings = _settings(tmp_path, hub_mode="school", hub_school_tenancy_enabled=True)
    assert settings.hub_mode == "school"
