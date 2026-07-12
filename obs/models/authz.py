"""Pydantic schemas for AuthZ owner administration."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActionName = Literal["read", "write", "generate", "activate"]
EffectName = Literal["allow", "deny"]
NodeTypeName = Literal["hierarchy", "datapoint", "logic_graph", "logic_capability"]
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


class AuthzPrincipalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: NodeTypeName
    node_id: str
    role: RoleName
    effect: EffectName = "allow"


class AuthzPrincipalGrantsReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[AuthzPrincipalGrant]

    @model_validator(mode="after")
    def reject_duplicate_targets(self) -> Self:
        targets = [(grant.node_type, grant.node_id) for grant in self.grants]
        if len(targets) != len(set(targets)):
            raise ValueError("Duplicate grants for the same node_type and node_id are not allowed")
        return self


class AuthzPrincipalReference(BaseModel):
    principal_type: PrincipalTypeName
    principal_id: str


class AuthzPrincipalGrantsResponse(BaseModel):
    principal: AuthzPrincipalReference
    grants: list[AuthzPrincipalGrant]
