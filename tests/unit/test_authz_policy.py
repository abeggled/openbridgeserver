from __future__ import annotations

from obs.api.auth import Principal
from obs.api.authz import AuthzAction, AuthzTarget, ControlClass, GrantEffect, Role, RoleGrant, authorize


def _user(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


def _grant(
    node_id: str,
    *,
    role: Role | str = Role.RESIDENT,
    effect: GrantEffect | str = GrantEffect.ALLOW,
    ancestors: tuple[str, ...] = (),
    principal_type: str = "user",
    principal_id: str = "alice",
    central_control: bool = False,
) -> RoleGrant:
    return RoleGrant(
        principal_type=principal_type,
        principal_id=principal_id,
        node_type="hierarchy",
        node_id=node_id,
        role=role,
        effect=effect,
        ancestors=ancestors,
        central_control=central_control,
    )


def _target(
    node_id: str,
    *,
    ancestors: tuple[str, ...] = (),
    min_role: Role | str | None = None,
    control_class: ControlClass | str = ControlClass.ROOM_LOCAL,
) -> AuthzTarget:
    return AuthzTarget(
        node_type="hierarchy",
        node_id=node_id,
        ancestors=ancestors,
        min_role=min_role,
        control_class=control_class,
    )


def test_admin_user_is_allowed_without_grants():
    decision = authorize(
        principal=_user(is_admin=True),
        action=AuthzAction.WRITE,
        targets=[_target("room")],
        grants=[],
    )

    assert decision.allowed is True
    assert decision.reason == "admin"


def test_no_targets_is_denied():
    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[], grants=[])

    assert decision.allowed is False
    assert decision.reason == "no_targets"


def test_guest_can_read_but_not_write():
    target = _target("room")
    grant = _grant("room", role=Role.GUEST)

    read = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])
    write = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[grant])

    assert read.allowed is True
    assert write.allowed is False
    assert write.reason == "missing_allow"


def test_central_plant_write_requires_explicit_scope_switch():
    target = _target("plant", control_class=ControlClass.CENTRAL_PLANT)

    denied = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[_grant("plant", role=Role.OPERATOR)])
    allowed = authorize(
        principal=_user(),
        action=AuthzAction.WRITE,
        targets=[target],
        grants=[_grant("plant", role=Role.OPERATOR, central_control=True)],
    )

    assert denied == type(denied)(False, "central_control_required")
    assert allowed.allowed is True


def test_central_scope_switch_never_overrides_explicit_deny():
    target = _target("plant", control_class="central_plant")
    decision = authorize(
        principal=_user(),
        action=AuthzAction.ACTIVATE,
        targets=[target],
        grants=[
            _grant("plant", role=Role.OPERATOR, central_control=True),
            _grant("plant", role=Role.GUEST, effect=GrantEffect.DENY),
        ],
    )

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_deny_beats_matching_allow():
    target = _target("room")
    grants = [
        _grant("room", role=Role.OWNER),
        _grant("room", role=Role.GUEST, effect=GrantEffect.DENY),
    ]

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=grants)

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_persisted_text_deny_beats_matching_allow():
    target = _target("room")
    grants = [
        _grant("room", role=Role.OWNER),
        _grant("room", role=Role.GUEST, effect="deny"),
    ]

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=grants)

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_read_inherits_upwards_from_assigned_child_node():
    grant = _grant("room", role=Role.GUEST, ancestors=("building", "floor"))
    target = _target("floor", ancestors=("building",))

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])

    assert decision.allowed is True


def test_read_inherits_downwards_from_assigned_parent_node():
    grant = _grant("floor", role=Role.GUEST, ancestors=("building",))
    target = _target("room", ancestors=("building", "floor"))

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])

    assert decision.allowed is True


def test_read_deny_inherits_downwards_from_assigned_parent_node():
    target = _target("room", ancestors=("building", "floor"))
    grants = [
        _grant("room", role=Role.GUEST, ancestors=("building", "floor")),
        _grant("floor", role=Role.GUEST, effect=GrantEffect.DENY, ancestors=("building",)),
    ]

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=grants)

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_literal_read_action_keeps_read_inheritance():
    grant = _grant("room", role=Role.GUEST, ancestors=("building", "floor"))
    target = _target("floor", ancestors=("building",))

    decision = authorize(principal=_user(), action="read", targets=[target], grants=[grant])

    assert decision.allowed is True


def test_write_inherits_downwards_from_assigned_parent_node():
    grant = _grant("floor", role=Role.RESIDENT, ancestors=("building",))
    target = _target("room", ancestors=("building", "floor"))

    decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[grant])

    assert decision.allowed is True


def test_write_does_not_inherit_upwards_from_child_node():
    grant = _grant("room", role=Role.OWNER, ancestors=("building", "floor"))
    target = _target("floor", ancestors=("building",))

    decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_read_uses_any_target_semantics():
    allowed = _target("room-a")
    missing = _target("room-b")
    grant = _grant("room-a", role=Role.GUEST)

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[allowed, missing], grants=[grant])

    assert decision.allowed is True


def test_read_without_matching_grant_is_denied():
    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[_target("room")], grants=[])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_grants_do_not_cross_node_types():
    target = AuthzTarget(node_type="visu", node_id="room")
    grant = _grant("room", role=Role.GUEST)

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[target], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_write_uses_all_target_semantics():
    allowed = _target("room-a")
    missing = _target("room-b")
    grant = _grant("room-a", role=Role.RESIDENT)

    decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[allowed, missing], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_control_class_gate_requires_minimum_role():
    target = _target("actuator", min_role=Role.OPERATOR)
    resident = _grant("actuator", role=Role.RESIDENT)
    operator = _grant("actuator", role=Role.OPERATOR)

    resident_decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[resident])
    operator_decision = authorize(principal=_user(), action=AuthzAction.WRITE, targets=[target], grants=[operator])

    assert resident_decision.allowed is False
    assert operator_decision.allowed is True


def test_persisted_text_roles_satisfy_minimum_role_gate():
    target = _target("actuator", min_role="operator")
    grant = _grant("actuator", role="operator")

    decision = authorize(principal=_user(), action="write", targets=[target], grants=[grant])

    assert decision.allowed is True


def test_api_key_principal_matches_raw_key_id_grant():
    principal = Principal(subject="api_key:3ff3e934-8d4d-45f6-b4d0-5f6f2272681d", type="api_key", is_admin=False)
    grant = _grant(
        "room",
        role=Role.GUEST,
        principal_type="api_key",
        principal_id="3ff3e934-8d4d-45f6-b4d0-5f6f2272681d",
    )

    decision = authorize(principal=principal, action=AuthzAction.READ, targets=[_target("room")], grants=[grant])

    assert decision.allowed is True


def test_user_principal_ignores_api_key_grant():
    grant = _grant("room", role=Role.GUEST, principal_type="api_key", principal_id="alice")

    decision = authorize(principal=_user(), action=AuthzAction.READ, targets=[_target("room")], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_api_key_principal_without_prefix_does_not_match_unrelated_grant():
    principal = Principal(subject="legacy-key-subject", type="api_key", is_admin=False)
    grant = _grant("room", role=Role.GUEST, principal_type="api_key", principal_id="different-key")

    decision = authorize(principal=principal, action=AuthzAction.READ, targets=[_target("room")], grants=[grant])

    assert decision.allowed is False
    assert decision.reason == "missing_allow"


def test_sibling_deny_does_not_block_allowed_ancestor():
    """DENY on sibling B must not deny the common ancestor when ALLOW on sibling A covers it."""
    # Hierarchy: root → A, root → B
    allow_a = _grant("A", role=Role.GUEST, ancestors=("root",))
    deny_b = _grant("B", role=Role.GUEST, effect=GrantEffect.DENY, ancestors=("root",))
    target_root = _target("root")

    decision = authorize(
        principal=_user(),
        action=AuthzAction.READ,
        targets=[target_root],
        grants=[allow_a, deny_b],
    )

    assert decision.allowed is True


def test_deny_still_cascades_downward_to_descendants():
    """A DENY on a parent must still deny descendants (downward cascade not broken)."""
    deny_floor = _grant("floor", role=Role.GUEST, effect=GrantEffect.DENY, ancestors=("building",))
    allow_floor = _grant("floor", role=Role.GUEST, ancestors=("building",))
    target_room = _target("room", ancestors=("building", "floor"))

    decision = authorize(
        principal=_user(),
        action=AuthzAction.READ,
        targets=[target_room],
        grants=[allow_floor, deny_floor],
    )

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"
