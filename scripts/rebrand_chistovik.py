from pathlib import Path
import re

ROOT = Path(".")
EXCLUDED = {
    Path("brand-audit.txt"),
    Path("brand-audit-after.txt"),
    Path("rebrand-status.json"),
    Path("rebrand-log.txt"),
    Path("rebrand-error.txt"),
}
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".json", ".html", ".md", ".txt",
    ".sh", ".bat", ".iss", ".spec", ".ini",
}


def write(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def edit(path: str | Path, replacements: list[tuple[str, str]]) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    write(target, text)


for path in ROOT.rglob("*"):
    if not path.is_file() or path in EXCLUDED or ".git" in path.parts:
        continue
    if path.parts[:2] == (".github", "workflows"):
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
        "README.md", ".env.example", ".env.production"
    }:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue

    protected = {
        "__PF_REPO__": "PaperFlow-Stream",
        "__PF_HEADER__": "X-PaperFlow",
    }
    for token, value in protected.items():
        text = text.replace(value, token)

    for old, new in (
        ("PaperFlow Hybrid Local Hub", "Чистовик"),
        ("PaperFlow Hybrid Hub", "Чистовик"),
        ("PaperFlow Personal Hub", "Чистовик"),
        ("PaperFlow Stream", "Чистовик"),
        ("PaperFlow Web", "Чистовик"),
        ("PaperFlow Hub", "Чистовик"),
    ):
        text = text.replace(old, new)
    text = re.sub(r"\bPaperFlow\b", "Чистовик", text)

    for token, value in protected.items():
        text = text.replace(token, value)

    text = text.replace("PaperFlowHubSetup", "ChistovikSetup")
    text = text.replace("PaperFlowHub.exe", "Chistovik.exe")
    text = text.replace("dist\\PaperFlowHub", "dist\\Chistovik")
    text = text.replace("dist/PaperFlowHub", "dist/Chistovik")
    text = text.replace("PaperFlowHub-Windows", "Chistovik-Windows")
    text = text.replace("paperflow-icon.svg", "chistovik-icon.svg")
    text = text.replace("paperflow-web-v1", "chistovik-web-v2")
    text = text.replace("paperflow.example.ru", "chistovik.example.ru")
    text = text.replace("web.paperflow.example", "web.chistovik.example")
    text = text.replace("paperflow.school.local", "chistovik.school.local")
    text = text.replace("paperflow-pilot.vercel.app", "chistovik-pilot.vercel.app")
    text = text.replace("app.paperflow.ru", "app.chistovik.ru")
    text = text.replace("paperflow-web", "chistovik-web")
    write(path, text)

# Keep the established data directory and protocol keys so existing data,
# browser pairing and installed clients remain compatible after rebranding.
edit("backend/app/desktop.py", [
    ('return root / "Чистовик"', 'return root / "PaperFlow"'),
    ('return Path.home() / "Library" / "Application Support" / "Чистовик"', 'return Path.home() / "Library" / "Application Support" / "PaperFlow"'),
    ('return payload.get("product") == "Чистовик"', 'return payload.get("product") in {"Чистовик", "PaperFlow Hub"}'),
    ('"PaperFlowHub",\n        image,', '"Chistovik",\n        image,'),
    ('description="Чистовик"', 'description="Локальный модуль «Чистовик»"'),
])

edit("frontend/src/hub/runtime.ts", [
    ('const DEFAULT_WORKSPACE = "personal";', 'const DEFAULT_WORKSPACE = "personal";\nconst SUPPORTED_PRODUCTS = new Set(["Чистовик", "PaperFlow Hub"]);'),
    ('if (info.product !== "Чистовик" || info.protocolVersion !== 1) {', 'if (!SUPPORTED_PRODUCTS.has(info.product) || info.protocolVersion !== 1) {'),
    ('Чистовик ответил с кодом', 'Локальный модуль «Чистовик» ответил с кодом'),
    ('Чистовик не найден', 'Локальный модуль «Чистовик» не найден'),
    ('Адрес Hub должен указывать', 'Адрес локального модуля должен указывать'),
    ('Чистовик ещё не подключён', 'Локальный модуль «Чистовик» ещё не подключён'),
])

edit("frontend/src/App.tsx", [
    ('Paper<span>Flow</span> Web', 'Чистовик'),
    ('Hub {hub.connection?.info.version}', 'Локальный модуль {hub.connection?.info.version}'),
    ('Отключить Hub', 'Отключить модуль'),
])

edit("frontend/src/hub/HubProvider.tsx", [
    ('const REQUIRED_HUB_VERSION = "0.3.1";', 'const REQUIRED_HUB_VERSION = "0.3.2";'),
    ('v0.3.1-pilot/ChistovikSetup-0.3.1.exe', 'v0.3.2-pilot/ChistovikSetup-0.3.2.exe'),
    ('releases/tag/v0.3.1-pilot', 'releases/tag/v0.3.2-pilot'),
    ('Hub найден, но этот адрес Чистовик ещё не подтверждён.', 'Локальный модуль найден, но этот адрес «Чистовика» ещё не подтверждён.'),
    ('Локальный Чистовик', 'Локальный модуль «Чистовик»'),
    ('Hub уже установлен — проверить', 'Чистовик уже установлен — проверить'),
    ('Ожидаю запуск Hub.', 'Ожидаю запуск «Чистовика».'),
    ('Hub найден.', 'Чистовик найден.'),
    ('Адрес локального Hub', 'Адрес локального модуля'),
    ('локальный Hub', 'локальный модуль'),
    ('PaperFlowHubSetup.exe', 'ChistovikSetup.exe'),
])

edit("frontend/index.html", [
    ('<meta name="theme-color" content="#15202b" />', '<meta name="theme-color" content="#171918" />'),
])

write("frontend/public/manifest.webmanifest", '''{
  "name": "Чистовик",
  "short_name": "Чистовик",
  "description": "Локальное сканирование и проверка письменных работ",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#f3f1ec",
  "theme_color": "#171918",
  "icons": [
    {
      "src": "/chistovik-icon.svg",
      "sizes": "any",
      "type": "image/svg+xml",
      "purpose": "any maskable"
    }
  ]
}
''')

write("frontend/public/chistovik-icon.svg", '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="Чистовик">
  <rect width="512" height="512" rx="96" fill="#F3F1EC"/>
  <path d="M112 104h72v136h144V104h72v304h-72v-96H112V104z" fill="#171918"/>
  <path d="M328 312h72v96h-72z" fill="#3157FF"/>
</svg>
''')
old_icon = Path("frontend/public/paperflow-icon.svg")
if old_icon.exists():
    old_icon.unlink()

edit("frontend/src/pages/SettingsPage.tsx", [('"paperflow_backup.zip"', '"chistovik_backup.zip"')])
edit("backend/app/api/routes_scan.py", [('f"paperflow_diagnostics_s{session_id}_{stamp}.zip"', 'f"chistovik_diagnostics_s{session_id}_{stamp}.zip"')])
edit("backend/app/api/routes_export.py", [('f"paperflow_{_stamp(session)}.', 'f"chistovik_{_stamp(session)}.')])
edit("backend/app/api/routes_maintenance.py", [('f"paperflow_backup_{stamp}.zip"', 'f"chistovik_backup_{stamp}.zip"')])

edit("backend/app/main.py", [
    ('description="Локальный контур Чистовик:', 'description="Локальный контур «Чистовика»:'),
    ('Откройте облачный Чистовик', 'Откройте веб-приложение «Чистовик»'),
])
edit("backend/app/config.py", [
    ('version: str = "0.3.1"', 'version: str = "0.3.2"'),
])
edit("backend/app/api/routes_hub.py", [
    ('<title>Подключение Чистовик</title>', '<title>Подключение к «Чистовику»</title>'),
    ('<h1>Подключение к Чистовик</h1>', '<h1>Подключение к «Чистовику»</h1>'),
    ('открытым Чистовик', 'открытым приложением «Чистовик»'),
    ('в окне Чистовик', 'в окне «Чистовика»'),
])

edit("packaging/windows/PaperFlowHub.spec", [('name="PaperFlowHub"', 'name="Chistovik"')])
edit("packaging/windows/PaperFlowHub.iss", [
    ('#define MyAppVersion "0.3.1"', '#define MyAppVersion "0.3.2"'),
    ('VersionInfoVersion=0.3.1.0', 'VersionInfoVersion=0.3.2.0'),
    ('Создать ярлык Чистовик', 'Создать ярлык «Чистовик»'),
    ('Запустить Чистовик', 'Запустить «Чистовик»'),
])

for path in [
    Path("frontend/.env.example"),
    Path("frontend/.env.production"),
    Path("docs/VERCEL_DEPLOYMENT.md"),
    Path("README.md"),
    Path("deploy/yandex-cloud/README.md"),
]:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    text = text.replace("v0.3.1-pilot", "v0.3.2-pilot")
    text = re.sub(r"ChistovikSetup-0\.3\.[01]", "ChistovikSetup-0.3.2", text)
    write(path, text)

edit("backend/tests/test_desktop_host.py", [('or "PaperFlowHub" in command', 'or "Chistovik" in command')])

for path in [Path("README.md"), Path("deploy/yandex-cloud/README.md"), Path("docs/VERCEL_DEPLOYMENT.md")]:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = text.replace("ChistovikSetup-0.3.0.exe", "ChistovikSetup-0.3.2.exe")
        text = text.replace("ChistovikSetup-0.3.0.exe.sha256", "ChistovikSetup-0.3.2.exe.sha256")
        write(path, text)
