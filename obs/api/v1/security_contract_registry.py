"""Machine-readable security and audit contracts for mutating v1 routes.

The registry is intentionally explicit.  A new ``config_mutation`` route must
declare its principal, authorization, scope, concealment, capability and audit
semantics before the route-classification anti-drift gate can pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, TypeAlias

from obs.api.v1.route_classification_registry import RouteSignature

AuthzActionName: TypeAlias = Literal["read", "write", "generate", "activate"]


class PrincipalMode(StrEnum):
    AUTH_FLOW = "auth_flow"
    CREDENTIAL = "credential"
    USER = "user"
    PRINCIPAL = "principal"
    ADMIN = "admin"


class AuthorizationMode(StrEnum):
    AUTH_FLOW = "auth_flow"
    CREDENTIAL = "credential"
    SELF = "self"
    ADMIN = "admin"
    POLICY = "policy"
    POLICY_OR_CAPABILITY = "policy_or_capability"


class CheckKind(StrEnum):
    ADMIN = "admin"
    SELF = "self"
    ROLE = "role"
    CAPABILITY = "capability"
    OWNERSHIP = "ownership"
    TARGET_AUDIENCE = "target_audience"


class ConcealmentMode(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"


class RootSemantics(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    ADMIN_ONLY = "admin_only"
    SCOPED_PARENT = "scoped_parent"


class AuditMode(StrEnum):
    ATOMIC = "atomic"
    RESULT = "result"
    SECURITY = "security"


class AuditEffect(StrEnum):
    DB_MUTATION = "db_mutation"
    EXTERNAL_MUTATION = "external_mutation"
    OPERATION = "operation"
    SECURITY_EVENT = "security_event"
    EXPORT = "export"


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    kind: CheckKind
    action: AuthzActionName | None
    target_type: str
    target_resolver: str
    capability: str | None = None


@dataclass(frozen=True, slots=True)
class RouteSecurityContract:
    principal: PrincipalMode
    authorization: AuthorizationMode
    action: AuthzActionName | None
    scope: str
    concealment: ConcealmentMode
    root: RootSemantics
    capability: str | None
    checks: tuple[PolicyCheck, ...]
    audit_action: str
    audit_mode: AuditMode
    audit_effect: AuditEffect
    allowed_detail_fields: frozenset[str]


def _contract(
    principal: PrincipalMode,
    authorization: AuthorizationMode,
    action: AuthzActionName | None,
    scope: str,
    audit_action: str,
    audit_mode: AuditMode,
    *,
    concealment: ConcealmentMode = ConcealmentMode.FORBIDDEN,
    root: RootSemantics = RootSemantics.NOT_APPLICABLE,
    capability: str | None = None,
    extra_checks: tuple[PolicyCheck, ...] = (),
    audit_effect: AuditEffect | None = None,
    allowed_detail_fields: tuple[str, ...] = (),
) -> RouteSecurityContract:
    checks: list[PolicyCheck] = []
    if action is not None:
        kind = {
            AuthorizationMode.ADMIN: CheckKind.ADMIN,
            AuthorizationMode.SELF: CheckKind.SELF,
        }.get(authorization, CheckKind.ROLE)
        checks.append(PolicyCheck(kind, action, scope, f"declared:{scope}"))
    if capability:
        checks.append(PolicyCheck(CheckKind.CAPABILITY, action, scope, f"declared:{scope}", capability))
    checks.extend(extra_checks)
    return RouteSecurityContract(
        principal=principal,
        authorization=authorization,
        action=action,
        scope=scope,
        concealment=concealment,
        root=root,
        capability=capability,
        checks=tuple(checks),
        audit_action=audit_action,
        audit_mode=audit_mode,
        audit_effect=audit_effect
        or {
            AuditMode.ATOMIC: AuditEffect.DB_MUTATION,
            AuditMode.RESULT: AuditEffect.OPERATION,
            AuditMode.SECURITY: AuditEffect.SECURITY_EVENT,
        }[audit_mode],
        allowed_detail_fields=frozenset(allowed_detail_fields),
    )


def _admin(
    scope: str,
    audit_action: str,
    *,
    result: bool = False,
    root: bool = False,
    audit_effect: AuditEffect | None = None,
    details: tuple[str, ...] = (),
) -> RouteSecurityContract:
    return _contract(
        PrincipalMode.ADMIN,
        AuthorizationMode.ADMIN,
        "write",
        scope,
        audit_action,
        AuditMode.RESULT if result else AuditMode.ATOMIC,
        root=RootSemantics.ADMIN_ONLY if root else RootSemantics.NOT_APPLICABLE,
        audit_effect=audit_effect,
        allowed_detail_fields=details,
    )


def _user(
    scope: str,
    audit_action: str,
    *,
    result: bool = False,
    audit_effect: AuditEffect | None = None,
    details: tuple[str, ...] = (),
) -> RouteSecurityContract:
    return _contract(
        PrincipalMode.USER,
        AuthorizationMode.SELF,
        "write",
        scope,
        audit_action,
        AuditMode.RESULT if result else AuditMode.ATOMIC,
        audit_effect=audit_effect,
        allowed_detail_fields=details,
    )


def _policy(
    scope: str,
    audit_action: str,
    *,
    action: AuthzActionName = "write",
    result: bool = False,
    root: RootSemantics = RootSemantics.NOT_APPLICABLE,
    capability: str | None = None,
    extra_checks: tuple[PolicyCheck, ...] = (),
    audit_effect: AuditEffect | None = None,
    details: tuple[str, ...] = (),
) -> RouteSecurityContract:
    return _contract(
        PrincipalMode.PRINCIPAL,
        AuthorizationMode.POLICY_OR_CAPABILITY if capability else AuthorizationMode.POLICY,
        action,
        scope,
        audit_action,
        AuditMode.RESULT if result else AuditMode.ATOMIC,
        concealment=ConcealmentMode.NOT_FOUND,
        root=root,
        capability=capability,
        extra_checks=extra_checks,
        audit_effect=audit_effect,
        allowed_detail_fields=details,
    )


ROUTE_SECURITY_CONTRACTS: Final[dict[RouteSignature, RouteSecurityContract]] = {
    # Authentication, principals, users and grants.
    ("POST", "/api/v1/auth/login"): _contract(
        PrincipalMode.AUTH_FLOW, AuthorizationMode.AUTH_FLOW, None, "session", "auth.session.login", AuditMode.SECURITY
    ),
    ("POST", "/api/v1/auth/refresh"): _contract(
        PrincipalMode.AUTH_FLOW, AuthorizationMode.AUTH_FLOW, None, "session", "auth.session.refresh", AuditMode.SECURITY
    ),
    ("POST", "/api/v1/auth/apikeys"): _policy("api_key", "auth.api_key.created", root=RootSemantics.SCOPED_PARENT),
    ("DELETE", "/api/v1/auth/apikeys/{key_id}"): _user("api_key", "auth.api_key.deleted"),
    ("PUT", "/api/v1/auth/apikeys/{key_id}/capabilities"): _admin("api_key_capabilities", "auth.api_key.capabilities_replaced"),
    ("POST", "/api/v1/auth/users"): _admin("user", "auth.user.created", root=True, details=("is_admin", "mqtt_enabled", "username")),
    ("PATCH", "/api/v1/auth/users/{username}"): _admin("user", "auth.user.updated", details=("after", "before", "changed_fields")),
    ("DELETE", "/api/v1/auth/users/{username}"): _admin(
        "user",
        "auth.user.deleted",
        details=("api_keys_revoked", "artifacts_transferred", "is_admin", "mqtt_enabled", "successor_username", "username"),
    ),
    ("POST", "/api/v1/auth/users/{username}/mqtt-password"): _user("mqtt_password", "auth.user.mqtt_password_set"),
    ("DELETE", "/api/v1/auth/users/{username}/mqtt-password"): _admin("mqtt_password", "auth.user.mqtt_password_deleted"),
    ("POST", "/api/v1/auth/me/change-password"): _user("password", "auth.user.password_changed"),
    ("PUT", "/api/v1/authz/principals/{principal_type}/{principal_id:path}/grants"): _admin(
        "authz_grants",
        "authz.grants.replaced",
        details=(
            "before_count",
            "after_count",
            "added_count",
            "removed_count",
            "updated_count",
            "unchanged_count",
            "before_sha256",
            "after_sha256",
            "changes",
        ),
    ),
    # Datapoints and bindings.
    ("POST", "/api/v1/datapoints/"): _admin("datapoint", "datapoint.created", root=True),
    ("PATCH", "/api/v1/datapoints/{dp_id}"): _policy(
        "datapoint", "datapoint.updated", capability="datapoint.metadata.write", details=("capability", "changed_fields", "before", "after")
    ),
    ("DELETE", "/api/v1/datapoints/{dp_id}"): _admin("datapoint", "datapoint.deleted"),
    ("POST", "/api/v1/datapoints/{dp_id}/bindings"): _policy(
        "binding",
        "binding.created",
        extra_checks=(PolicyCheck(CheckKind.ROLE, "write", "adapter_instance", "derived:binding_adapter_instance"),),
    ),
    ("PATCH", "/api/v1/datapoints/{dp_id}/bindings/{binding_id}"): _policy(
        "binding",
        "binding.updated",
        extra_checks=(PolicyCheck(CheckKind.ROLE, "write", "adapter_instance", "derived:binding_adapter_instance"),),
    ),
    ("DELETE", "/api/v1/datapoints/{dp_id}/bindings/{binding_id}"): _policy(
        "binding",
        "binding.deleted",
        extra_checks=(PolicyCheck(CheckKind.ROLE, "write", "adapter_instance", "derived:binding_adapter_instance"),),
    ),
    # URL policy and adapters.
    ("POST", "/api/v1/security/url-target-allowlist"): _admin(
        "url_target_allowlist",
        "security.url_target.created",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
    ),
    ("DELETE", "/api/v1/security/url-target-allowlist"): _admin(
        "url_target_allowlist",
        "security.url_target.deleted",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
    ),
    ("POST", "/api/v1/adapters/instances"): _admin("adapter_instance", "adapter.instance.created", root=True),
    ("PATCH", "/api/v1/adapters/instances/{instance_id}"): _policy("adapter_instance", "adapter.instance.updated"),
    ("DELETE", "/api/v1/adapters/instances/{instance_id}"): _policy("adapter_instance", "adapter.instance.deleted"),
    ("POST", "/api/v1/adapters/instances/{instance_id}/test"): _policy("adapter_instance", "adapter.instance.tested", result=True),
    ("POST", "/api/v1/adapters/instances/{instance_id}/restart"): _policy("adapter_instance", "adapter.instance.restarted", result=True),
    ("POST", "/api/v1/adapters/instances/{source_instance_id}/bindings/migrate"): _policy(
        "adapter_instance", "adapter.bindings.migrated", details=("resource_count", "payload_sha256")
    ),
    ("POST", "/api/v1/adapters/instances/{instance_id}/iobroker/import-preview"): _policy(
        "adapter_instance",
        "adapter.iobroker.import_previewed",
        result=True,
        capability="adapter.declared",
        details=("resource_count", "payload_sha256"),
    ),
    ("POST", "/api/v1/adapters/instances/{instance_id}/iobroker/import"): _policy(
        "adapter_instance",
        "adapter.iobroker.imported",
        capability="adapter.create_datapoint+link_binding",
        details=("resource_count", "payload_sha256"),
    ),
    ("POST", "/api/v1/adapters/instances/{instance_id}/anwesenheit/sync-bindings"): _policy(
        "adapter_instance",
        "adapter.anwesenheit.bindings_synced",
        capability="adapter.link_binding",
        details=("resource_count", "payload_sha256"),
    ),
    ("POST", "/api/v1/adapters/{adapter_type}/test"): _admin("adapter_type", "adapter.type.tested", result=True),
    ("PATCH", "/api/v1/adapters/{adapter_type}/config"): _admin("adapter_type", "adapter.type.config_updated"),
    # Central settings, support, RingBuffer and backup/config lifecycle.
    ("PUT", "/api/v1/system/settings"): _user("app_settings", "system.settings.updated", details=("after", "before", "changed_fields")),
    ("PUT", "/api/v1/system/history/settings"): _admin(
        "history_settings",
        "system.history.settings_updated",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=(
            "plugin",
            "default_window_hours",
            "influx_version",
        ),
    ),
    ("POST", "/api/v1/system/history/test"): _admin("history_settings", "system.history.connection_tested", result=True),
    ("POST", "/api/v1/system/nav-links"): _admin("nav_link", "system.nav_link.created"),
    ("PATCH", "/api/v1/system/nav-links/{link_id}"): _admin("nav_link", "system.nav_link.updated"),
    ("DELETE", "/api/v1/system/nav-links/{link_id}"): _admin("nav_link", "system.nav_link.deleted"),
    ("PUT", "/api/v1/system/log-level"): _admin("log_level", "system.log_level.updated", result=True),
    ("POST", "/api/v1/support/debug-log"): _admin("debug_log", "support.debug_log.enabled", result=True),
    ("DELETE", "/api/v1/support/debug-log"): _admin("debug_log", "support.debug_log.disabled", result=True),
    ("POST", "/api/v1/ringbuffer/filtersets"): _policy(
        "ringbuffer_filterset", "ringbuffer.filterset.created", action="generate", root=RootSemantics.SCOPED_PARENT
    ),
    ("PUT", "/api/v1/ringbuffer/filtersets/{filterset_id}"): _policy("ringbuffer_filterset", "ringbuffer.filterset.updated"),
    ("DELETE", "/api/v1/ringbuffer/filtersets/{filterset_id}"): _policy("ringbuffer_filterset", "ringbuffer.filterset.deleted"),
    ("POST", "/api/v1/ringbuffer/filtersets/{filterset_id}/clone"): _policy(
        "ringbuffer_filterset", "ringbuffer.filterset.cloned", action="generate", root=RootSemantics.SCOPED_PARENT
    ),
    ("PATCH", "/api/v1/ringbuffer/filtersets/order"): _policy(
        "ringbuffer_filterset", "ringbuffer.filtersets.reordered", details=("item_count", "payload_sha256")
    ),
    ("PATCH", "/api/v1/ringbuffer/filtersets/{filterset_id}/topbar"): _policy("ringbuffer_filterset", "ringbuffer.filterset.topbar_updated"),
    ("PUT", "/api/v1/ringbuffer/export/settings"): _user("ringbuffer_export_settings", "ringbuffer.export.settings_updated"),
    ("POST", "/api/v1/ringbuffer/config"): _admin(
        "ringbuffer_config",
        "ringbuffer.config.updated",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
    ),
    ("POST", "/api/v1/config/import/db"): _admin(
        "database_config", "config.database.imported", result=True, audit_effect=AuditEffect.EXTERNAL_MUTATION
    ),
    ("POST", "/api/v1/config/import"): _admin(
        "configuration",
        "config.imported",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("counts", "error_count", "payload_sha256"),
    ),
    ("DELETE", "/api/v1/config/reset"): _admin(
        "configuration",
        "config.factory_reset",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("counts", "error_count"),
    ),
    ("DELETE", "/api/v1/config/reset/bindings"): _admin(
        "bindings",
        "config.bindings_cleared",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("counts", "error_count"),
    ),
    ("DELETE", "/api/v1/config/reset/datapoints"): _admin(
        "datapoints",
        "config.datapoints_cleared",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("counts", "error_count"),
    ),
    ("DELETE", "/api/v1/config/reset/logic"): _admin(
        "logic_graphs",
        "config.logic_cleared",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("counts", "error_count"),
    ),
    ("DELETE", "/api/v1/config/reset/adapters"): _admin("adapters", "config.adapters_cleared", result=True, details=("counts", "error_count")),
    ("PUT", "/api/v1/config/autobackup/config"): _admin("autobackup_config", "autobackup.config_updated"),
    ("POST", "/api/v1/config/autobackup/run"): _admin("autobackup", "autobackup.run", result=True, audit_effect=AuditEffect.EXTERNAL_MUTATION),
    ("POST", "/api/v1/config/autobackup/restore/{name}"): _admin(
        "autobackup",
        "autobackup.restored",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("counts", "error_count"),
    ),
    ("DELETE", "/api/v1/config/autobackup/{name}"): _admin(
        "autobackup", "autobackup.deleted", result=True, audit_effect=AuditEffect.EXTERNAL_MUTATION
    ),
    # KNX, Logic and Visu.
    ("POST", "/api/v1/knxproj/import"): _admin("knx_project", "knx.project.imported", result=True, details=("resource_count", "payload_sha256")),
    ("POST", "/api/v1/knxproj/import-csv"): _admin(
        "knx_group_addresses", "knx.group_addresses.imported", result=True, details=("resource_count", "payload_sha256")
    ),
    ("PUT", "/api/v1/knxproj/devices/{pa}/hierarchy-links"): _admin(
        "knx_device", "knx.device.hierarchy_links_updated", details=("resource_count", "payload_sha256")
    ),
    ("DELETE", "/api/v1/knxproj/group-addresses"): _user(
        "knx_group_addresses", "knx.group_addresses.cleared", details=("resource_count", "payload_sha256")
    ),
    ("POST", "/api/v1/knx/keyfile"): _admin("knx_keyfile", "knx.keyfile.uploaded", result=True, audit_effect=AuditEffect.EXTERNAL_MUTATION),
    ("DELETE", "/api/v1/knx/keyfile/{file_id}"): _admin(
        "knx_keyfile", "knx.keyfile.deleted", result=True, audit_effect=AuditEffect.EXTERNAL_MUTATION
    ),
    ("POST", "/api/v1/logic/graphs"): _policy(
        "logic_graph",
        "logic.graph.created",
        action="generate",
        root=RootSemantics.SCOPED_PARENT,
        extra_checks=(PolicyCheck(CheckKind.CAPABILITY, "generate", "logic_capability", "constant:create_graph", "create_graph"),),
        details=("control_class", "creator_grant_role", "delegated", "enabled_persisted", "enabled_requested", "operation", "reason"),
    ),
    ("PUT", "/api/v1/logic/graphs/{graph_id}"): _policy("logic_graph", "logic.graph.updated"),
    ("PATCH", "/api/v1/logic/graphs/{graph_id}"): _policy("logic_graph", "logic.graph.patched"),
    ("DELETE", "/api/v1/logic/graphs/{graph_id}"): _policy("logic_graph", "logic.graph.deleted"),
    ("POST", "/api/v1/logic/graphs/import"): _policy(
        "logic_graph",
        "logic.graph.imported",
        action="generate",
        root=RootSemantics.SCOPED_PARENT,
        extra_checks=(PolicyCheck(CheckKind.CAPABILITY, "generate", "logic_capability", "constant:create_graph", "create_graph"),),
        details=("control_class", "creator_grant_role", "delegated", "enabled_persisted", "enabled_requested", "operation", "reason"),
    ),
    ("POST", "/api/v1/logic/graphs/{graph_id}/run"): _policy(
        "logic_graph",
        "logic.graph.run",
        action="activate",
        result=True,
        extra_checks=(
            PolicyCheck(CheckKind.ROLE, "read", "datapoint", "derived:logic_flow_datapoints"),
            PolicyCheck(CheckKind.CAPABILITY, "activate", "logic_capability", "derived:logic_node_capabilities", "logic.declared"),
        ),
        details=("control_class", "denied_checks", "output_count", "warning_count"),
    ),
    ("POST", "/api/v1/logic/graphs/{graph_id}/duplicate"): _policy(
        "logic_graph",
        "logic.graph.duplicated",
        action="generate",
        root=RootSemantics.SCOPED_PARENT,
        extra_checks=(
            PolicyCheck(CheckKind.ROLE, "read", "logic_graph", "path:graph_id"),
            PolicyCheck(CheckKind.CAPABILITY, "generate", "logic_capability", "constant:create_graph", "create_graph"),
        ),
        details=("control_class", "creator_grant_role", "delegated", "enabled_persisted", "enabled_requested", "operation", "reason"),
    ),
    ("POST", "/api/v1/visu/nodes/import"): _policy(
        "visu_node",
        "visu.node.imported",
        action="generate",
        root=RootSemantics.SCOPED_PARENT,
        extra_checks=(PolicyCheck(CheckKind.ROLE, "generate", "datapoint", "derived:visu_referenced_datapoints"),),
        details=("node_count", "operation"),
    ),
    ("POST", "/api/v1/visu/nodes"): _policy(
        "visu_node",
        "visu.node.created",
        action="generate",
        root=RootSemantics.SCOPED_PARENT,
        extra_checks=(PolicyCheck(CheckKind.ROLE, "generate", "datapoint", "derived:visu_referenced_datapoints"),),
    ),
    ("PATCH", "/api/v1/visu/nodes/{node_id}"): _policy("visu_node", "visu.node.updated"),
    ("DELETE", "/api/v1/visu/nodes/{node_id}"): _policy("visu_node", "visu.node.deleted"),
    ("POST", "/api/v1/visu/nodes/{node_id}/copy"): _policy(
        "visu_node",
        "visu.node.copied",
        action="generate",
        root=RootSemantics.SCOPED_PARENT,
        extra_checks=(
            PolicyCheck(CheckKind.ROLE, "read", "visu_node", "path:node_id"),
            PolicyCheck(CheckKind.ROLE, "generate", "datapoint", "derived:visu_referenced_datapoints"),
        ),
        details=("node_count", "operation", "source_node_id"),
    ),
    ("PUT", "/api/v1/visu/nodes/{node_id}/move"): _policy("visu_node", "visu.node.moved"),
    ("POST", "/api/v1/visu/nodes/{node_id}/auth"): _contract(
        PrincipalMode.CREDENTIAL,
        AuthorizationMode.CREDENTIAL,
        None,
        "visu_page_credential",
        "visu.page.credential_checked",
        AuditMode.SECURITY,
        concealment=ConcealmentMode.NOT_FOUND,
    ),
    ("PUT", "/api/v1/visu/pages/{node_id}"): _policy("visu_page", "visu.page.updated", capability="visu.page_config.write"),
    ("PUT", "/api/v1/visu/nodes/{node_id}/users"): _policy("visu_page_audience", "visu.page.audience_updated"),
    ("POST", "/api/v1/visu/backgrounds/import"): _admin(
        "visu_background",
        "visu.backgrounds.imported",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("imported_count", "skipped_count"),
    ),
    ("DELETE", "/api/v1/visu/backgrounds"): _user(
        "visu_background",
        "visu.backgrounds.deleted",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("deleted_count", "not_found_count", "requested_count"),
    ),
    # Icons and hierarchy.
    ("POST", "/api/v1/icons/import"): _admin(
        "icon_set",
        "icons.imported",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("resource_count", "payload_sha256"),
    ),
    ("POST", "/api/v1/icons/export"): _user(
        "icon_set", "icons.exported", result=True, audit_effect=AuditEffect.EXPORT, details=("resource_count", "payload_sha256")
    ),
    ("DELETE", "/api/v1/icons/"): _admin(
        "icon_set",
        "icons.deleted",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("resource_count", "payload_sha256"),
    ),
    ("PUT", "/api/v1/icons/settings"): _user("icon_settings", "icons.settings_updated"),
    ("POST", "/api/v1/icons/fontawesome"): _admin(
        "icon_set",
        "icons.fontawesome_imported",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("resource_count", "payload_sha256"),
    ),
    ("POST", "/api/v1/icons/knxuf"): _user(
        "icon_set",
        "icons.knxuf_imported",
        result=True,
        audit_effect=AuditEffect.EXTERNAL_MUTATION,
        details=("resource_count", "payload_sha256"),
    ),
    ("POST", "/api/v1/hierarchy/trees"): _admin("hierarchy_tree", "hierarchy.tree.created", root=True),
    ("PUT", "/api/v1/hierarchy/trees/{tree_id}"): _admin("hierarchy_tree", "hierarchy.tree.updated"),
    ("DELETE", "/api/v1/hierarchy/trees/{tree_id}"): _admin("hierarchy_tree", "hierarchy.tree.deleted"),
    ("POST", "/api/v1/hierarchy/nodes"): _admin("hierarchy_node", "hierarchy.node.created", root=True),
    ("PUT", "/api/v1/hierarchy/nodes/{node_id}"): _admin("hierarchy_node", "hierarchy.node.updated"),
    ("PUT", "/api/v1/hierarchy/nodes/{node_id}/move"): _admin("hierarchy_node", "hierarchy.node.moved"),
    ("DELETE", "/api/v1/hierarchy/nodes/{node_id}"): _admin("hierarchy_node", "hierarchy.node.deleted"),
    ("POST", "/api/v1/hierarchy/links"): _admin("hierarchy_link", "hierarchy.link.created"),
    ("DELETE", "/api/v1/hierarchy/links"): _admin("hierarchy_link", "hierarchy.link.deleted"),
    ("POST", "/api/v1/hierarchy/import-from-ets"): _admin(
        "hierarchy", "hierarchy.ets_imported", result=True, details=("resource_count", "payload_sha256")
    ),
}


def get_route_security_contract(method: str, path: str) -> RouteSecurityContract:
    """Return the declared contract or fail closed for unknown mutations."""
    try:
        return ROUTE_SECURITY_CONTRACTS[(method.upper(), path)]
    except KeyError as exc:
        raise LookupError(f"No security contract for {method.upper()} {path}") from exc
