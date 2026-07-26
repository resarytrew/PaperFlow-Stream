"""Strict metadata-only contracts for the future Yandex Cloud control plane.

This module deliberately contains no free-form payloads. Student names, class
names, images, OCR text, answers, tasks and marks therefore have nowhere to be
placed in an outbound cloud request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _CloudContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CloudUpdateCheck(_CloudContract):
    installation_id: str = Field(min_length=8, max_length=80)
    hub_version: str = Field(min_length=1, max_length=32)
    protocol_version: int = Field(default=1, ge=1, le=100)
    platform: Literal["windows", "linux", "macos", "unknown"] = "unknown"
    architecture: Literal["x86_64", "arm64", "x86", "unknown"] = "unknown"
    release_channel: Literal["stable", "beta"] = "stable"


class CloudLicenseCheck(_CloudContract):
    installation_id: str = Field(min_length=8, max_length=80)
    license_token_hash: str = Field(min_length=32, max_length=128)
    hub_version: str = Field(min_length=1, max_length=32)


class CloudTechnicalEvent(_CloudContract):
    installation_id: str = Field(min_length=8, max_length=80)
    event: Literal[
        "hub_started",
        "update_available",
        "update_installed",
        "update_failed",
        "license_valid",
        "license_invalid",
    ]
    hub_version: str = Field(min_length=1, max_length=32)
    error_code: str | None = Field(default=None, max_length=80)
