"""Composition-owned issuers for the public room and Dashboard projection DTOs.

HTTP operator authentication stays at the ASGI boundary.  These smaller
credentials only prevent a platform adapter from minting a signed projection
request after it has received the host object.  The caller cannot choose
world, cursor, viewer kind, permission, or redaction policy.

The grant authority must be supplied to the World application at construction
time; adapters bind later onto that already-open source.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Final

from .dashboard_home_snapshot import _ROOM_ROUTES
from .dashboard_projection_adapter import (
    DashboardProjectionAdapter,
    DashboardPublicProjectionAdapter,
    DashboardPublicRouteCatalog,
    DashboardRoomRouteCatalog,
    RoomProjectionSource,
)
from .projection import (
    AuthenticatedProjectionPrincipal,
    ProjectionAuthority,
    ProjectionCapabilityIssuer,
    ProjectionGrant,
)
from .schemas import ProjectionRequest


_PUBLIC_ACTIVITY_LABELS: Final = {
    "focused_work": "在看资料",
    "relax": "放松一下",
}


class CompositionDashboardPrincipalVerifier:
    """Authenticate the composition-only reader, never an HTTP caller."""

    def __init__(
        self,
        *,
        world_id: str,
        principal_id: str,
        authentication_context: str,
    ) -> None:
        self._world_id = world_id
        self._principal_id = principal_id
        self._authentication_context = authentication_context
        self._credential = object()

    @property
    def credential(self) -> object:
        return self._credential

    def authenticate(self, credential: object) -> AuthenticatedProjectionPrincipal:
        if credential is not self._credential:
            raise PermissionError("dashboard projection credential is not composition-owned")
        return AuthenticatedProjectionPrincipal(
            principal_id=self._principal_id,
            world_id=self._world_id,
            authentication_context=self._authentication_context,
        )


class CompositionDashboardRequestIssuer:
    """Mint exactly one fixed viewer capability owned by this composition."""

    def __init__(
        self,
        *,
        world_id: str,
        issuer: ProjectionCapabilityIssuer,
        credential: object,
        viewer_id: str,
        viewer_kind: str,
        redaction_policy: str,
        request_prefix: str,
    ) -> None:
        self._world_id = world_id
        self._issuer = issuer
        self._credential = credential
        self._viewer_id = viewer_id
        self._viewer_kind = viewer_kind
        self._redaction_policy = redaction_policy
        self._request_prefix = request_prefix

    def issue(self) -> ProjectionRequest:
        nonce = secrets.token_hex(16)
        request = ProjectionRequest(
            schema_version="world-v2.1",
            request_id=f"request:{self._request_prefix}-{self._viewer_kind}:{nonce}",
            world_id=self._world_id,
            viewer_kind=self._viewer_kind,
            viewer_id=self._viewer_id,
            permissions=frozenset(),
            trace_id=f"trace:{self._request_prefix}-{self._viewer_kind}:{nonce}",
            redaction_policy=self._redaction_policy,
        )
        return self._issuer.bind(request, credential=self._credential)


@dataclass(frozen=True, slots=True)
class DashboardProjectionPlan:
    """Grants and issuers that must exist before the World application opens."""

    authority: ProjectionAuthority
    room_issuer: CompositionDashboardRequestIssuer
    public_issuer: CompositionDashboardRequestIssuer
    room_routes: DashboardRoomRouteCatalog


@dataclass(frozen=True, slots=True)
class DashboardProjectionComposition:
    """Adapters plus the issuers that mint their one allowed projection request."""

    capture: DashboardProjectionAdapter
    public_capture: DashboardPublicProjectionAdapter
    room_issuer: CompositionDashboardRequestIssuer
    public_issuer: CompositionDashboardRequestIssuer


def plan_dashboard_projection_lane(
    *,
    world_id: str,
    lane: str,
    room_routes: DashboardRoomRouteCatalog | None = None,
) -> DashboardProjectionPlan:
    """Create the composition-owned grants before the SQLite application opens."""

    if not world_id:
        raise ValueError("dashboard projection composition requires a world id")
    if not lane:
        raise ValueError("dashboard projection composition requires a lane name")
    routes = room_routes if room_routes is not None else _ROOM_ROUTES
    room_viewer_id = f"dashboard:{lane}-room"
    public_viewer_id = f"dashboard:{lane}-public"
    authentication_context = f"world-v2:{lane}-dashboard-composition.1"
    room_principal = CompositionDashboardPrincipalVerifier(
        world_id=world_id,
        principal_id=room_viewer_id,
        authentication_context=authentication_context,
    )
    public_principal = CompositionDashboardPrincipalVerifier(
        world_id=world_id,
        principal_id=public_viewer_id,
        authentication_context=authentication_context,
    )
    authority = ProjectionAuthority(
        grants=(
            ProjectionGrant(
                world_id=world_id,
                viewer_id=room_viewer_id,
                viewer_kind="room_renderer",
                permissions=frozenset(),
                redaction_policy="room-public-v1",
            ),
            ProjectionGrant(
                world_id=world_id,
                viewer_id=public_viewer_id,
                viewer_kind="dashboard_public",
                permissions=frozenset(),
                redaction_policy="dashboard-public-v1",
            ),
        )
    )
    return DashboardProjectionPlan(
        authority=authority,
        room_issuer=CompositionDashboardRequestIssuer(
            world_id=world_id,
            issuer=ProjectionCapabilityIssuer(
                authority=authority,
                principal_verifier=room_principal,
            ),
            credential=room_principal.credential,
            viewer_id=room_viewer_id,
            viewer_kind="room_renderer",
            redaction_policy="room-public-v1",
            request_prefix=lane,
        ),
        public_issuer=CompositionDashboardRequestIssuer(
            world_id=world_id,
            issuer=ProjectionCapabilityIssuer(
                authority=authority,
                principal_verifier=public_principal,
            ),
            credential=public_principal.credential,
            viewer_id=public_viewer_id,
            viewer_kind="dashboard_public",
            redaction_policy="dashboard-public-v1",
            request_prefix=lane,
        ),
        room_routes=routes,
    )


def bind_dashboard_projection_lane(
    *,
    plan: DashboardProjectionPlan,
    source: RoomProjectionSource,
) -> DashboardProjectionComposition:
    """Attach read-only DTO adapters to an already-open World application."""

    return DashboardProjectionComposition(
        capture=DashboardProjectionAdapter(source=source, routes=plan.room_routes),
        public_capture=DashboardPublicProjectionAdapter(
            source=source,
            routes=DashboardPublicRouteCatalog(
                room_routes=plan.room_routes,
                activity_labels=_PUBLIC_ACTIVITY_LABELS,
            ),
        ),
        room_issuer=plan.room_issuer,
        public_issuer=plan.public_issuer,
    )


__all__ = [
    "CompositionDashboardPrincipalVerifier",
    "CompositionDashboardRequestIssuer",
    "DashboardProjectionComposition",
    "DashboardProjectionPlan",
    "bind_dashboard_projection_lane",
    "plan_dashboard_projection_lane",
]
