"""Pydantic schemas for AuthZ owner UI previews."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActionName = Literal["read", "write", "generate", "activate"]
EffectName = Literal["allow", "deny"]
NodeTypeName = Literal["hierarchy", "datapoint"]
PrincipalTypeName = Literal["user", "api_key"]
RoleName = Literal["owner", "resident", "operator", "guest"]


class AuthzPreviewPrincipal(BaseModel):
    principal_type: PrincipalTypeName = "user"
    principal_id: str
    is_admin: bool | None = None


class AuthzPreviewGrant(BaseModel):
    principal_type: PrincipalTypeName = "user"
    principal_id: str
    node_type: NodeTypeName
    node_id: str
    role: RoleName
    effect: EffectName = "allow"


class AuthzPreviewTarget(BaseModel):
    node_type: NodeTypeName
    node_id: str
    min_role: RoleName | None = None


class AuthzPreviewRequest(BaseModel):
    principal: AuthzPreviewPrincipal
    actions: list[ActionName] = Field(default_factory=lambda: ["read", "write", "activate", "generate"])
    targets: list[AuthzPreviewTarget]
    draft_grants: list[AuthzPreviewGrant] = Field(default_factory=list)
    include_persisted: bool = True


class AuthzPreviewResolvedTarget(BaseModel):
    node_type: str
    node_id: str
    ancestors: list[str] = Field(default_factory=list)
    min_role: RoleName | None = None


class AuthzPreviewResult(BaseModel):
    action: ActionName
    node_type: NodeTypeName
    node_id: str
    allowed: bool
    reason: str
    reason_text: str
    effective_role: RoleName | None = None
    required_role: RoleName | None = None
    resolved_targets: list[AuthzPreviewResolvedTarget] = Field(default_factory=list)
    matching_grants: list[AuthzPreviewGrant] = Field(default_factory=list)


class AuthzPreviewResponse(BaseModel):
    principal: AuthzPreviewPrincipal
    results: list[AuthzPreviewResult]
