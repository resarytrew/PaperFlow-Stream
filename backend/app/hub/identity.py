"""Persistent Hub identity, pairing challenges and origin-bound client tokens."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HubClient:
    id: str
    name: str
    origin: str
    workspace_id: str
    actor_id: str
    role: str
    created_at: str
    expires_at: str
    last_seen_at: str

    def public_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class _Challenge:
    id: str
    code: str
    origin: str
    client_name: str
    workspace_id: str
    expires_at: datetime


class HubIdentityStore:
    """Small local credential store independent from the application database.

    Keeping pairing credentials outside SQLite makes database restores and
    future per-school database migrations unable to accidentally clone trusted
    browser sessions.
    """

    def __init__(self, settings: Settings, root: Path | None = None) -> None:
        self.settings = settings
        self.root = root or settings.hub_dir
        self.path = self.root / "identity.json"
        self._lock = threading.RLock()
        self._challenges: dict[str, _Challenge] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._data = self._load_or_create()

    @property
    def installation_id(self) -> str:
        return str(self._data["installation_id"])

    def _load_or_create(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("installation_id"):
                    data.setdefault("clients", [])
                    return data
            except (OSError, json.JSONDecodeError):
                pass

        data = {
            "schema_version": 1,
            "installation_id": str(uuid.uuid4()),
            "created_at": _iso(_utcnow()),
            "clients": [],
        }
        self._write(data)
        return data

    def _write(self, data: dict[str, Any] | None = None) -> None:
        payload = data if data is not None else self._data
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _prune(self) -> None:
        now = _utcnow()
        self._challenges = {
            key: value for key, value in self._challenges.items() if value.expires_at > now
        }
        clients = []
        for item in self._data.get("clients", []):
            try:
                if _parse_time(str(item["expires_at"])) > now:
                    clients.append(item)
            except (KeyError, TypeError, ValueError):
                continue
        if len(clients) != len(self._data.get("clients", [])):
            self._data["clients"] = clients
            self._write()

    def start_pairing(self, *, origin: str, client_name: str, workspace_id: str) -> _Challenge:
        with self._lock:
            self._prune()
            challenge = _Challenge(
                id=secrets.token_urlsafe(18),
                code=f"{secrets.randbelow(1_000_000):06d}",
                origin=origin,
                client_name=(client_name.strip() or "PaperFlow Web")[:120],
                workspace_id=workspace_id,
                expires_at=_utcnow() + timedelta(seconds=self.settings.hub_pairing_code_ttl_seconds),
            )
            self._challenges[challenge.id] = challenge
            return challenge

    def pending_pairing_code(self, challenge_id: str) -> str | None:
        """Return a code for the local confirmation page or future tray app."""
        with self._lock:
            self._prune()
            challenge = self._challenges.get(challenge_id)
            return challenge.code if challenge else None

    def confirm_pairing(
        self,
        *,
        challenge_id: str,
        code: str,
        origin: str,
        workspace_id: str,
    ) -> tuple[str, HubClient]:
        with self._lock:
            self._prune()
            challenge = self._challenges.get(challenge_id)
            if challenge is None:
                raise ValueError("Код подключения истёк или не существует")
            if not hmac.compare_digest(challenge.origin, origin):
                raise ValueError("Источник подключения не совпадает")
            if not hmac.compare_digest(challenge.workspace_id, workspace_id):
                raise ValueError("Рабочее пространство не совпадает")
            if not hmac.compare_digest(challenge.code, code.strip()):
                raise ValueError("Неверный код подключения")

            token = secrets.token_urlsafe(48)
            now = _utcnow()
            client_id = str(uuid.uuid4())
            client = HubClient(
                id=client_id,
                name=challenge.client_name,
                origin=origin,
                workspace_id=workspace_id,
                actor_id="local-owner" if self.settings.hub_mode == "personal" else f"client:{client_id}",
                role="owner" if self.settings.hub_mode == "personal" else "teacher",
                created_at=_iso(now),
                expires_at=_iso(now + timedelta(days=self.settings.hub_token_ttl_days)),
                last_seen_at=_iso(now),
            )
            record = {
                **client.public_dict(),
                "token_hash": _token_hash(token),
            }
            clients = [
                item
                for item in self._data.get("clients", [])
                if not (
                    item.get("origin") == origin
                    and item.get("workspace_id") == workspace_id
                    and item.get("name") == client.name
                )
            ]
            clients.append(record)
            self._data["clients"] = clients[-100:]
            self._challenges.pop(challenge_id, None)
            self._write()
            return token, client

    def verify_token(self, token: str, *, origin: str, workspace_id: str) -> HubClient | None:
        if not token:
            return None
        digest = _token_hash(token)
        with self._lock:
            self._prune()
            for item in self._data.get("clients", []):
                stored_hash = str(item.get("token_hash", ""))
                if not hmac.compare_digest(stored_hash, digest):
                    continue
                if item.get("origin") != origin or item.get("workspace_id") != workspace_id:
                    return None
                item["last_seen_at"] = _iso(_utcnow())
                try:
                    client = HubClient(
                        id=str(item["id"]),
                        name=str(item["name"]),
                        origin=str(item["origin"]),
                        workspace_id=str(item["workspace_id"]),
                        actor_id=str(item["actor_id"]),
                        role=str(item["role"]),
                        created_at=str(item["created_at"]),
                        expires_at=str(item["expires_at"]),
                        last_seen_at=str(item["last_seen_at"]),
                    )
                except KeyError:
                    return None
                self._write()
                return client
        return None

    def list_clients(self) -> list[dict[str, str]]:
        with self._lock:
            self._prune()
            result: list[dict[str, str]] = []
            for item in self._data.get("clients", []):
                public = {key: value for key, value in item.items() if key != "token_hash"}
                result.append({key: str(value) for key, value in public.items()})
            return result

    def revoke_client(self, client_id: str) -> bool:
        with self._lock:
            clients = self._data.get("clients", [])
            filtered = [item for item in clients if item.get("id") != client_id]
            changed = len(filtered) != len(clients)
            if changed:
                self._data["clients"] = filtered
                self._write()
            return changed


@lru_cache(maxsize=1)
def get_hub_identity_store() -> HubIdentityStore:
    return HubIdentityStore(get_settings())
