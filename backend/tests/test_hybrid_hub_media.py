"""Browser-specific media authorization regression tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.hub.identity import HubIdentityStore
from app.middleware.hub_security import HubSecurityMiddleware


def test_cross_site_img_without_origin_requires_read_only_media_token(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        hub_allowed_origins=["https://web.paperflow.example"],
        hub_trusted_unpaired_origins=[],
        hub_public_url="https://127.0.0.1:17841",
    )
    identity = HubIdentityStore(settings, tmp_path / "hub")
    app = FastAPI()
    app.add_middleware(HubSecurityMiddleware, settings=settings, identity=identity)

    @app.get("/api/sheets/1/image/thumbnail")
    def media_endpoint() -> dict:
        return {"image": True}

    @app.get("/api/private")
    def private_endpoint() -> dict:
        return {"private": True}

    challenge = identity.start_pairing(
        origin="https://web.paperflow.example",
        client_name="Teacher browser",
        workspace_id="personal",
    )
    api_token, media_token, _ = identity.confirm_pairing(
        challenge_id=challenge.id,
        code=challenge.code,
        origin="https://web.paperflow.example",
        workspace_id="personal",
    )

    client = TestClient(app)
    browser_headers = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "image",
    }

    assert client.get(
        f"/api/sheets/1/image/thumbnail?workspace=personal&hub_token={media_token}",
        headers=browser_headers,
    ).status_code == 200
    assert client.get(
        f"/api/sheets/1/image/thumbnail?workspace=personal&hub_token={api_token}",
        headers=browser_headers,
    ).status_code == 401
    assert client.get(
        "/api/sheets/1/image/thumbnail?workspace=personal",
        headers=browser_headers,
    ).status_code == 401
    assert client.get(
        "/api/private",
        headers={"Sec-Fetch-Site": "cross-site"},
    ).status_code == 403
