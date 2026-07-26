"""Request-scoped identity for personal and future school Hub deployments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Request


HubRole = Literal["owner", "admin", "teacher", "viewer", "local"]


@dataclass(frozen=True, slots=True)
class HubRequestContext:
    """Identity and workspace selected for one API request.

    Personal Hub uses the fixed ``personal`` workspace and ``local-owner``
    actor. School Hub will populate the same contract from authenticated users
    and workspace membership without changing route signatures.
    """

    installation_id: str
    deployment_mode: str
    workspace_id: str
    actor_id: str
    role: HubRole
    client_id: str | None
    origin: str | None
    authenticated: bool


def get_hub_context(request: Request) -> HubRequestContext:
    raw = getattr(request.state, "hub_context", None)
    if isinstance(raw, HubRequestContext):
        return raw
    raise RuntimeError("Hub security middleware did not provide request context")


HubContext = Annotated[HubRequestContext, Depends(get_hub_context)]
