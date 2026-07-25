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


def load_config(db: Session, *, use_cache: bool = True) -> RuntimeConfig:
    """Return the effective runtime configuration."""
    global _cache
    if use_cache and _cache is not None:
        return _cache

    row = db.execute(select(AppSetting).limit(1)).scalar_one_or_none()
    defaults = RuntimeConfig().model_dump()
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
    """Merge ``patch`` into the stored configuration and validate it."""
    global _cache
    row = db.execute(select(AppSetting).limit(1)).scalar_one_or_none()
    current = dict(row.payload) if row and row.payload else {}
    merged_payload = _deep_merge(current, patch)

    # Validate against the full schema before persisting.
    effective = RuntimeConfig.model_validate(_deep_merge(RuntimeConfig().model_dump(), merged_payload))

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
