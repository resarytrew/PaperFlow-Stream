"""Desktop host for the Personal PaperFlow Hub.

The module is intentionally a thin process supervisor around the existing
FastAPI application. It can run from source or as a PyInstaller executable,
keeps the Hub bound to loopback, manages per-user autostart on Windows and
provides a small system-tray menu.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PORT = 17841
DEFAULT_HOST = "127.0.0.1"


def default_data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root / "PaperFlow"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PaperFlow"
    root = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "paperflow"


def desktop_config_path(data_dir: Path) -> Path:
    return data_dir / "hub" / "desktop.json"


def load_desktop_config(data_dir: Path) -> dict:
    path = desktop_config_path(data_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_desktop_config(data_dir: Path, payload: dict) -> None:
    path = desktop_config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def executable_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    return [str(Path(sys.executable).resolve()), "-m", "app.desktop"]


def windows_autostart_command() -> str:
    return subprocess.list2cmdline([*executable_command(), "--background"])


def install_windows_autostart() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Автозапуск через реестр поддерживается только в Windows")
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "PaperFlowHub", 0, winreg.REG_SZ, windows_autostart_command())


def uninstall_windows_autostart() -> None:
    if sys.platform != "win32":
        return
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "PaperFlowHub")
    except FileNotFoundError:
        pass


def cloud_origin(web_url: str) -> str | None:
    try:
        parsed = urlsplit(web_url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def configure_environment(*, data_dir: Path, port: int, web_url: str) -> None:
    local_url = f"http://{DEFAULT_HOST}:{port}"
    os.environ.setdefault("PAPERFLOW_DATA_DIR", str(data_dir))
    os.environ.setdefault("PAPERFLOW_HUB_MODE", "personal")
    os.environ.setdefault("PAPERFLOW_HUB_PORT", str(port))
    os.environ.setdefault("PAPERFLOW_HUB_BIND_HOST", DEFAULT_HOST)
    os.environ.setdefault("PAPERFLOW_HUB_PUBLIC_URL", local_url)

    origin = cloud_origin(web_url)
    if origin and origin != local_url and "PAPERFLOW_HUB_ALLOWED_ORIGINS" not in os.environ:
        os.environ["PAPERFLOW_HUB_ALLOWED_ORIGINS"] = json.dumps([origin])


def hub_is_running(port: int) -> bool:
    try:
        with socket.create_connection((DEFAULT_HOST, port), timeout=0.35):
            pass
        with urllib.request.urlopen(f"http://{DEFAULT_HOST}:{port}/api/health", timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("product") == "PaperFlow Hub"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def wait_until_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if hub_is_running(port):
            return True
        time.sleep(0.2)
    return False


def open_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(data_dir)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(data_dir)])
    else:
        subprocess.Popen(["xdg-open", str(data_dir)])


def build_tray_icon():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), "#15202b")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 59, 59), radius=14, fill="#15202b")
    draw.rectangle((18, 15, 27, 49), fill="#f6f8fa")
    draw.rectangle((25, 15, 46, 24), fill="#f6f8fa")
    draw.rectangle((25, 31, 43, 40), fill="#55d6be")
    return image


def run_tray(server, *, web_url: str, local_url: str, data_dir: Path) -> None:
    try:
        import pystray
    except ImportError:
        server.run()
        return

    thread = threading.Thread(target=server.run, name="paperflow-hub", daemon=True)
    thread.start()
    if not wait_until_ready(server.config.port):
        server.should_exit = True
        raise RuntimeError("PaperFlow Hub не запустился")

    def open_web(_icon=None, _item=None) -> None:
        webbrowser.open(web_url)

    def backup(_icon=None, _item=None) -> None:
        webbrowser.open(f"{local_url}/api/maintenance/backup")

    def open_folder(_icon=None, _item=None) -> None:
        open_data_dir(data_dir)

    def quit_app(icon, _item=None) -> None:
        server.should_exit = True
        icon.stop()

    icon = pystray.Icon(
        "PaperFlowHub",
        build_tray_icon(),
        "PaperFlow Hub — работает локально",
        menu=pystray.Menu(
            pystray.MenuItem("Открыть PaperFlow", open_web, default=True),
            pystray.MenuItem("Создать резервную копию", backup),
            pystray.MenuItem("Открыть папку данных", open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Остановить Hub", quit_app),
        ),
    )
    webbrowser.open(web_url)
    icon.run()
    thread.join(timeout=10)


def run_server(*, port: int, web_url: str, data_dir: Path, background: bool) -> int:
    if hub_is_running(port):
        if not background:
            webbrowser.open(web_url)
        return 0

    configure_environment(data_dir=data_dir, port=port, web_url=web_url)
    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=DEFAULT_HOST,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    local_url = f"http://{DEFAULT_HOST}:{port}"

    if background:
        run_tray(server, web_url=web_url, local_url=local_url, data_dir=data_dir)
    else:
        thread = threading.Thread(target=server.run, name="paperflow-hub", daemon=True)
        thread.start()
        if not wait_until_ready(port):
            server.should_exit = True
            raise RuntimeError("PaperFlow Hub не запустился")
        webbrowser.open(web_url)
        try:
            while thread.is_alive():
                thread.join(timeout=0.5)
        except KeyboardInterrupt:
            server.should_exit = True
            thread.join(timeout=10)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PaperFlow Personal Hub")
    parser.add_argument("--port", type=int, default=int(os.getenv("PAPERFLOW_HUB_PORT", DEFAULT_PORT)))
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--web-url", default="")
    parser.add_argument("--set-web-url", default="")
    parser.add_argument("--background", action="store_true", help="Запустить с системным треем")
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--uninstall-autostart", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    saved = load_desktop_config(data_dir)

    if args.set_web_url:
        saved["web_url"] = args.set_web_url
        save_desktop_config(data_dir, saved)
        return 0

    if args.install_autostart:
        install_windows_autostart()
        return 0
    if args.uninstall_autostart:
        uninstall_windows_autostart()
        return 0

    local_url = f"http://{DEFAULT_HOST}:{args.port}"
    web_url = args.web_url or str(saved.get("web_url") or os.getenv("PAPERFLOW_WEB_URL") or local_url)
    return run_server(port=args.port, web_url=web_url, data_dir=data_dir, background=args.background)


if __name__ == "__main__":
    raise SystemExit(main())
