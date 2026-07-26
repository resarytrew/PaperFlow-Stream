"""Unit tests for the lightweight Personal Hub desktop supervisor."""

from __future__ import annotations

import json

from app.desktop import (
    cloud_origin,
    configure_environment,
    load_desktop_config,
    save_desktop_config,
    windows_autostart_command,
)


def test_desktop_config_roundtrip(tmp_path):
    save_desktop_config(tmp_path, {"web_url": "https://paperflow.example.ru"})
    assert load_desktop_config(tmp_path) == {"web_url": "https://paperflow.example.ru"}


def test_cloud_origin_accepts_only_http_web_urls():
    assert cloud_origin("https://paperflow.example.ru/app") == "https://paperflow.example.ru"
    assert cloud_origin("http://127.0.0.1:17841") == "http://127.0.0.1:17841"
    assert cloud_origin("file:///tmp/index.html") is None
    assert cloud_origin("not-a-url") is None


def test_configure_environment_allows_only_the_selected_web_origin(tmp_path, monkeypatch):
    for name in (
        "PAPERFLOW_DATA_DIR",
        "PAPERFLOW_HUB_MODE",
        "PAPERFLOW_HUB_PORT",
        "PAPERFLOW_HUB_BIND_HOST",
        "PAPERFLOW_HUB_PUBLIC_URL",
        "PAPERFLOW_HUB_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    configure_environment(
        data_dir=tmp_path,
        port=17841,
        web_url="https://paperflow.example.ru/workspace",
    )

    assert json.loads(__import__("os").environ["PAPERFLOW_HUB_ALLOWED_ORIGINS"]) == [
        "https://paperflow.example.ru"
    ]
    assert __import__("os").environ["PAPERFLOW_HUB_BIND_HOST"] == "127.0.0.1"


def test_autostart_command_uses_background_mode():
    command = windows_autostart_command()
    assert "--background" in command
    assert "app.desktop" in command or "PaperFlowHub" in command
