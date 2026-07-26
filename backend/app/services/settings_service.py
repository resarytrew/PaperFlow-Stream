"""Runtime configuration persistence (layered over code defaults)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import RuntimeConfig
from app.models import AppSetting

logger = logging.getLogger(__name__)

_cache: RuntimeConfig | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _defaults() -> dict:
    """Return complete defaults for internal validation/persistence only."""
    return RuntimeConfig().model_dump(include_secrets=True)


def load_config(db: Session, *, use_cache: bool = True) -> RuntimeConfig:
    """Return the effective runtime configuration."""
    global _cache
    if use_cache and _cache is not None:
        return _cache

    row = db.execute(select(AppSetting).limit(1)).scalar_one_or_none()
    defaults = _defaults()
    if row and row.payload:
        try:
            merged = _deep_merge(defaults, row.payload)
            config = RuntimeConfig.model_validate(merged)
        except Exception as exc:
            logger.warning("stored settings invalid (%s) – falling back to defaults", exc)
            config = RuntimeConfig()
    else:
        config = RuntimeConfig()

    _cache = config
    return config


def save_config(db: Session, patch: dict[str, Any]) -> RuntimeConfig:
    """Merge ``patch`` into the stored configuration and validate it.

    An empty API key means "leave the existing secret unchanged". This lets the
    settings API return a redacted value without a normal save erasing the key.
    """
    global _cache
    row = db.execute(select(AppSetting).limit(1)).scalar_one_or_none()
    current = dict(row.payload) if row and row.payload else {}

    vision_patch = patch.get("vision_ocr")
    if isinstance(vision_patch, dict):
        vision_patch = dict(vision_patch)
        vision_patch.pop("api_key_configured", None)
        if vision_patch.get("api_key") == "":
            vision_patch.pop("api_key", None)
        patch = dict(patch)
        patch["vision_ocr"] = vision_patch

    merged_payload = _deep_merge(current, patch)

    # Validate against the full schema before persisting.
    effective = RuntimeConfig.model_validate(_deep_merge(_defaults(), merged_payload))

    if row is None:
        row = AppSetting(payload=merged_payload)
        db.add(row)
    else:
        row.payload = merged_payload
    db.commit()

    _cache = effective
    return effective


def reset_config(db: Session) -> RuntimeConfig:
    """Restore safe defaults."""
    global _cache
    row = db.execute(select(AppSetting).limit(1)).scalar_one_or_none()
    if row is not None:
        row.payload = {}
        db.commit()
    _cache = RuntimeConfig()
    return _cache


def invalidate_cache() -> None:
    global _cache
    _cache = None
