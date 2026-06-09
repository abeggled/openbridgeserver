"""Central authorization policy primitives for role-per-node access."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal

from obs.api.auth import Principal


class Role(str, Enum):
    OWNER = "owner"
    RESIDENT = "resident"
    OPERATOR = "operator"
    GUEST = "guest"


class AuthzAction(str, Enum):
    READ = "read"
    WRITE = "write"
    GENERATE = "generate"
    ACTIVATE = "activate"


class GrantEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


RoleName = Literal["owner", "resident", "operator", "guest"]
ActionName = Literal["read", "write", "generate", "activate"]
EffectName = Literal["allow", "deny"]
PrincipalType = Literal["user", "api_key"]


_ROLE_RANK: dict[Role, int] = {
    Role.GUEST: 0,
    Role.RESIDENT: 1,
    Role.OPERATOR: 2,
    Role.OWNER: 3,
}

_ROLE_ACTIONS: dict[Role, frozenset[AuthzAction]] = {
    Role.OWNER: frozenset(AuthzAction),
    Role.OPERATOR: frozenset(AuthzAction),
    Role.RESIDENT: frozenset({AuthzAction.READ, AuthzAction.WRITE, AuthzAction.ACTIVATE}),
    Role.GUEST: frozenset({AuthzAction.READ}),
}


@dataclass(frozen=True)
class AuthzTarget:
    node_type: str
    node_id: str
    ancestors: tuple[str, ...] = ()
    min_role: Role | RoleName | None = None

    def __post_init__(self) -> None:
        if self.min_role is not None:
            object.__setattr__(self, "min_role", Role(self.min_role))

    @property
    def path(self) -> tuple[str, ...]:
        return (*self.ancestors, self.node_id)


@dataclass(frozen=True)
class RoleGrant:
    principal_type: PrincipalType
    principal_id: str
    node_type: str
    node_id: str
    role: Role | RoleName
    effect: GrantEffect | EffectName = GrantEffect.ALLOW
    ancestors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", Role(self.role))
        object.__setattr__(self, "effect", GrantEffect(self.effect))

    @property
    def path(self) -> tuple[str, ...]:
        return (*self.ancestors, self.node_id)


@dataclass(frozen=True)
class AuthzDecision:
    allowed: bool
    reason: str


def authorize(
    *,
    principal: Principal,
    action: AuthzAction | ActionName,
    targets: Iterable[AuthzTarget],
    grants: Iterable[RoleGrant],
) -> AuthzDecision:
    """Evaluate a policy decision for one request.

    Phase 2 keeps the existing admin bit as an owner-equivalent bridge while
    role assignments are rolled out across modules.
    """
    target_list = tuple(targets)
    if not target_list:
        return AuthzDecision(False, "no_targets")

    if principal.type == "user" and principal.is_admin:
        return AuthzDecision(True, "admin")

    action_value = AuthzAction(action)
    grant_list = tuple(grant for grant in grants if _matches_principal(principal, grant))
    target_decisions = tuple(_authorize_target(action=action_value, target=target, grants=grant_list) for target in target_list)

    if any(decision.reason == "explicit_deny" for decision in target_decisions):
        return AuthzDecision(False, "explicit_deny")

    if action_value == AuthzAction.READ:
        if any(decision.allowed for decision in target_decisions):
            return AuthzDecision(True, "allowed")
        return AuthzDecision(False, "missing_allow")

    if all(decision.allowed for decision in target_decisions):
        return AuthzDecision(True, "allowed")
    return AuthzDecision(False, "missing_allow")


def _matches_principal(principal: Principal, grant: RoleGrant) -> bool:
    if grant.principal_type != principal.type:
        return False
    if grant.principal_id == principal.subject:
        return True
    if principal.type == "api_key" and principal.subject.startswith("api_key:"):
        return grant.principal_id == principal.subject.removeprefix("api_key:")
    return False


def _authorize_target(*, action: AuthzAction, target: AuthzTarget, grants: Iterable[RoleGrant]) -> AuthzDecision:
    matching_grants = tuple(grant for grant in grants if _grant_applies(action=action, grant=grant, target=target))
    if any(grant.effect == GrantEffect.DENY for grant in matching_grants):
        return AuthzDecision(False, "explicit_deny")

    if any(_role_allows(role=grant.role, action=action, target=target) for grant in matching_grants):
        return AuthzDecision(True, "allowed")
    return AuthzDecision(False, "missing_allow")


def _grant_applies(*, action: AuthzAction, grant: RoleGrant, target: AuthzTarget) -> bool:
    if grant.node_type != target.node_type:
        return False
    if action == AuthzAction.READ:
        return target.node_id in grant.path or grant.node_id in target.path
    return grant.node_id in target.path


def _role_allows(*, role: Role, action: AuthzAction, target: AuthzTarget) -> bool:
    if action not in _ROLE_ACTIONS[role]:
        return False
    if target.min_role is None:
        return True
    return _ROLE_RANK[role] >= _ROLE_RANK[target.min_role]
