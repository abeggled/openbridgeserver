"""Generate Yahka/Apple Home mapping previews from the OBS VISU model.

The generator intentionally starts with a reviewable mapping before it writes
anything. It provides stable state names, explicit binding directions, status-GA
metadata, and warnings where manual review is still needed.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from obs.db.database import Database


BindingDirection = Literal["BOTH", "FROM_OBS"]
ObsBindingDirection = Literal["BOTH", "DEST"]


class HomeKitStateSpec(BaseModel):
    id: str
    data_type: str
    binding_direction: BindingDirection
    obs_binding_direction: ObsBindingDirection
    persistent: bool = True
    initial_value: Any = None
    notes: list[str] = Field(default_factory=list)


class HomeKitObsSpec(BaseModel):
    switch_dp_id: str | None = None
    status_dp_id: str | None = None
    status_confirms_write: bool = False
    optimistic_update: bool = False
    binding_direction: BindingDirection | None = None
    knx_write_ga: str | None = None
    knx_status_ga: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class HomeKitFallbackSpec(BaseModel):
    enabled: bool = False
    knx_write_ga: str | None = None
    knx_status_ga: str | None = None
    notes: list[str] = Field(default_factory=list)


class HomeKitAccessorySpec(BaseModel):
    name_key: str
    apple_home_name: str
    type: str
    widget_id: str | None = None
    widget_type: str | None = None
    states: dict[str, HomeKitStateSpec] = Field(default_factory=dict)
    obs: HomeKitObsSpec = Field(default_factory=HomeKitObsSpec)
    fallback: HomeKitFallbackSpec = Field(default_factory=HomeKitFallbackSpec)
    warnings: list[str] = Field(default_factory=list)


class HomeKitRoomSpec(BaseModel):
    floor: str
    room_key: str
    room_display_name: str
    apple_home_room: str
    accessories: list[HomeKitAccessorySpec] = Field(default_factory=list)


class HomeKitPreviewSummary(BaseModel):
    rooms: int = 0
    accessories: int = 0
    lightbulb: int = 0
    outlet: int = 0
    switch: int = 0
    contact_sensor: int = 0
    window_covering: int = 0
    thermostat: int = 0
    temperature_sensor: int = 0
    humidity_sensor: int = 0
    unsupported: int = 0
    needs_manual_review: int = 0
    exceeds_single_bridge_limit: bool = False
    recommended_bridges: int = 1


class HomeKitSystemStateSpec(BaseModel):
    id: str
    data_type: str
    writer: str
    source: str | None = None
    notes: list[str] = Field(default_factory=list)


class HomeKitPreview(BaseModel):
    project: str
    source_visu: str
    leading_iobroker: dict[str, Any]
    room_strategy: str
    accessory_limit_per_bridge: int = 150
    rooms: list[HomeKitRoomSpec] = Field(default_factory=list)
    summary: HomeKitPreviewSummary
    warnings: list[str] = Field(default_factory=list)
    system_states: list[HomeKitSystemStateSpec] = Field(default_factory=list)
    backup_items: list[str] = Field(default_factory=list)
    restore_tests: list[str] = Field(default_factory=list)


class HomeKitPreviewOptions(BaseModel):
    project: str = "OBS Home"
    root_node_id: str | None = None
    source_visu_name: str = "Home"
    leading_iobroker_name: str = "ioBroker"
    leading_iobroker_host: str = "localhost"
    leading_iobroker_port: int = 8082
    room_strategy: Literal["floor_prefix", "homekit_zones"] = "floor_prefix"
    accessory_limit_per_bridge: int = Field(default=150, ge=1)
    namespace_prefix: str = "0_userdata.0.obs.home"


class HomeKitApplyRequest(HomeKitPreviewOptions):
    iobroker_instance_id: uuid.UUID
    dry_run: bool = True
    create_iobroker_states: bool = False
    room_keys: list[str] = Field(default_factory=list)
    include_unsupported: bool = False


class HomeKitApplyItem(BaseModel):
    state_id: str
    room: str
    accessory: str
    accessory_type: str
    state_key: str
    data_type: str
    binding_direction: BindingDirection
    obs_binding_direction: ObsBindingDirection
    action: Literal[
        "create", "reuse_existing", "skip_existing", "skip_unsupported", "error"
    ]
    datapoint_id: str | None = None
    binding_id: str | None = None
    iobroker_state_created: bool = False
    message: str | None = None


class HomeKitApplyResult(BaseModel):
    dry_run: bool
    created_datapoints: int = 0
    created_bindings: int = 0
    created_iobroker_states: int = 0
    skipped_existing: int = 0
    skipped_unsupported: int = 0
    errors: list[str] = Field(default_factory=list)
    items: list[HomeKitApplyItem] = Field(default_factory=list)
    preview_summary: HomeKitPreviewSummary
    apply_summary: HomeKitPreviewSummary = Field(default_factory=HomeKitPreviewSummary)


_UMLAUTS = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "Ä": "ae",
    "Ö": "oe",
    "Ü": "ue",
}


def slugify(value: str, fallback: str = "item") -> str:
    value = "".join(_UMLAUTS.get(ch, ch) for ch in value.strip())
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    value = re.sub(r"_+", "_", value)
    return value or fallback


def _state_id(
    opts: HomeKitPreviewOptions, floor: str, room: str, accessory: str, prop: str
) -> str:
    return ".".join(
        [
            opts.namespace_prefix.rstrip("."),
            slugify(floor),
            slugify(room),
            slugify(accessory),
            slugify(prop),
        ]
    )


def _state(
    opts: HomeKitPreviewOptions,
    floor: str,
    room: str,
    accessory: str,
    prop: str,
    data_type: str,
    direction: BindingDirection,
    initial_value: Any = None,
    notes: list[str] | None = None,
) -> HomeKitStateSpec:
    return HomeKitStateSpec(
        id=_state_id(opts, floor, room, accessory, prop),
        data_type=data_type,
        binding_direction=direction,
        obs_binding_direction="BOTH" if direction == "BOTH" else "DEST",
        initial_value=initial_value,
        notes=notes or [],
    )


def _display_label(widget: dict[str, Any], default: str) -> str:
    config = widget.get("config") or {}
    for key in ("label", "name", "title"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = widget.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _dp_id(widget: dict[str, Any], *keys: str) -> str | None:
    config = widget.get("config") or {}
    for key in keys:
        value = config.get(key)
        if value:
            return str(value)
    value = widget.get("datapoint_id")
    if value:
        return str(value)
    return None


def _status_dp_id(widget: dict[str, Any], *keys: str) -> str | None:
    config = widget.get("config") or {}
    for key in keys:
        value = config.get(key)
        if value:
            return str(value)
    value = widget.get("status_datapoint_id")
    if value:
        return str(value)
    return None


def _bool_opt(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


async def _load_dp_index(db: Database) -> dict[str, dict[str, Any]]:
    rows = await db.fetchall("SELECT * FROM datapoints")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[row["id"]] = {
            "id": row["id"],
            "name": row["name"],
            "data_type": row["data_type"],
            "unit": row["unit"],
            "tags": json.loads(row["tags"] or "[]"),
        }
    return out


async def _load_knx_index(db: Database) -> dict[str, dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE adapter_type='KNX' AND enabled=1"
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            cfg = json.loads(row["config"] or "{}")
        except Exception:
            cfg = {}
        out.setdefault(row["datapoint_id"], cfg)
    return out


async def _load_visu_rows(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall("SELECT * FROM visu_nodes ORDER BY node_order ASC")
    return [dict(row) for row in rows]


def _find_root(
    rows: list[dict[str, Any]], opts: HomeKitPreviewOptions
) -> dict[str, Any] | None:
    if opts.root_node_id:
        return next((r for r in rows if r["id"] == opts.root_node_id), None)
    return next((r for r in rows if r["name"] == opts.source_visu_name), None)


def _descendants(rows: list[dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        children[row["parent_id"]].append(row)

    result: list[dict[str, Any]] = []
    stack = list(children.get(root_id, []))
    while stack:
        row = stack.pop(0)
        result.append(row)
        stack[0:0] = children.get(row["id"], [])
    return result


def _path_for(
    node: dict[str, Any], rows_by_id: dict[str, dict[str, Any]], root_id: str
) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = [node]
    parent_id = node.get("parent_id")
    while parent_id and parent_id != root_id and parent_id in rows_by_id:
        parent = rows_by_id[parent_id]
        path.insert(0, parent)
        parent_id = parent.get("parent_id")
    return path


def _page_widgets(row: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        pc = json.loads(row.get("page_config") or "{}")
    except Exception:
        return []
    widgets = pc.get("widgets", [])
    return widgets if isinstance(widgets, list) else []


def _knx_ga(
    knx_index: dict[str, dict[str, Any]], dp_id: str | None
) -> tuple[str | None, str | None]:
    if not dp_id:
        return None, None
    cfg = knx_index.get(dp_id) or {}
    return cfg.get("group_address"), cfg.get("state_group_address")


def _set_obs_from_dps(
    acc: HomeKitAccessorySpec,
    switch_dp: str | None,
    status_dp: str | None,
    knx_index: dict[str, dict[str, Any]],
) -> None:
    write_ga, write_status_ga = _knx_ga(knx_index, switch_dp)
    status_ga, status_state_ga = _knx_ga(knx_index, status_dp)
    acc.obs.switch_dp_id = switch_dp
    acc.obs.status_dp_id = status_dp
    acc.obs.status_confirms_write = bool(status_dp)
    acc.obs.optimistic_update = not bool(status_dp)
    acc.obs.knx_write_ga = write_ga
    acc.obs.knx_status_ga = status_ga or write_status_ga or status_state_ga
    acc.obs.binding_direction = "BOTH"
    acc.fallback.knx_write_ga = acc.obs.knx_write_ga
    acc.fallback.knx_status_ga = acc.obs.knx_status_ga
    acc.fallback.enabled = bool(acc.obs.knx_write_ga and acc.obs.knx_status_ga)
    if not status_dp:
        acc.warnings.append(
            "Keine Status-DP/Status-GA erkannt: optimistic_update waere erforderlich."
        )


def _light_accessory(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
    knx_index: dict[str, dict[str, Any]],
) -> HomeKitAccessorySpec:
    label = _display_label(widget, "Licht")
    cfg = widget.get("config") or {}
    acc = HomeKitAccessorySpec(
        name_key=slugify(label),
        apple_home_name=label,
        type="Lightbulb",
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
    )
    switch_dp = _dp_id(widget, "dp_switch")
    status_dp = _status_dp_id(widget, "dp_switch_status")
    _set_obs_from_dps(acc, switch_dp, status_dp, knx_index)
    acc.states["on"] = _state(opts, floor, room, label, "on", "boolean", "BOTH")
    if cfg.get("dp_dim"):
        acc.states["brightness"] = _state(
            opts, floor, room, label, "brightness", "number", "BOTH"
        )
        acc.obs.extra["brightness_dp_id"] = cfg.get("dp_dim")
        acc.obs.extra["brightness_status_dp_id"] = cfg.get("dp_dim_status") or None
    return acc


def _toggle_accessory(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
    knx_index: dict[str, dict[str, Any]],
    dp_index: dict[str, dict[str, Any]],
) -> HomeKitAccessorySpec:
    label = _display_label(widget, "Schalter")
    dp = _dp_id(widget)
    dp_name = (dp_index.get(dp or "", {}).get("name") or "").lower()
    haystack = f"{label} {dp_name}".lower()
    acc_type = (
        "Outlet"
        if any(part in haystack for part in ("steck", "socket", "outlet", "plug"))
        else "Switch"
    )
    acc = HomeKitAccessorySpec(
        name_key=slugify(label),
        apple_home_name=label,
        type=acc_type,
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
    )
    status_dp = _status_dp_id(widget)
    _set_obs_from_dps(acc, dp, status_dp, knx_index)
    acc.states["on"] = _state(opts, floor, room, label, "on", "boolean", "BOTH")
    if acc_type == "Outlet":
        acc.states["outlet_in_use"] = _state(
            opts,
            floor,
            room,
            label,
            "outlet_in_use",
            "boolean",
            "FROM_OBS",
            initial_value=True,
            notes=["Statisch true, wenn kein echter Verbrauchssensor vorhanden ist."],
        )
    return acc


def _contact_accessories(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
) -> list[HomeKitAccessorySpec]:
    label = _display_label(widget, "Fenster")
    cfg = widget.get("config") or {}
    specs = [
        ("contact", "dp_contact", "invert_contact", label),
        ("tilt", "dp_tilt", "invert_tilt", f"{label} Kipp"),
        ("contact_left", "dp_contact_left", "invert_contact_left", f"{label} links"),
        (
            "contact_right",
            "dp_contact_right",
            "invert_contact_right",
            f"{label} rechts",
        ),
    ]
    result: list[HomeKitAccessorySpec] = []
    for prop, dp_key, invert_key, acc_label in specs:
        dp = cfg.get(dp_key)
        if not dp:
            continue
        acc = HomeKitAccessorySpec(
            name_key=slugify(acc_label),
            apple_home_name=acc_label,
            type="ContactSensor",
            widget_id=widget.get("id"),
            widget_type=widget.get("type"),
        )
        acc.states["contact"] = _state(
            opts, floor, room, acc_label, "contact", "boolean", "FROM_OBS"
        )
        acc.obs.status_dp_id = str(dp)
        acc.obs.binding_direction = "FROM_OBS"
        acc.obs.extra["invert"] = _bool_opt(cfg.get(invert_key))
        acc.obs.extra["source"] = prop
        result.append(acc)
    if result:
        return result
    acc = HomeKitAccessorySpec(
        name_key=slugify(label),
        apple_home_name=label,
        type="ContactSensor",
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
        warnings=["Kein Kontakt-Datenpunkt im Fenster-Widget erkannt."],
    )
    acc.states["contact"] = _state(
        opts, floor, room, label, "contact", "boolean", "FROM_OBS"
    )
    return [acc]


def _rolladen_accessory(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
    knx_index: dict[str, dict[str, Any]],
) -> HomeKitAccessorySpec:
    label = _display_label(widget, "Rollo")
    cfg = widget.get("config") or {}
    acc = HomeKitAccessorySpec(
        name_key=slugify(label),
        apple_home_name=label,
        type="WindowCovering",
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
    )
    target_dp = cfg.get("dp_position")
    status_dp = cfg.get("dp_position_status") or target_dp
    _set_obs_from_dps(
        acc,
        str(target_dp) if target_dp else None,
        str(status_dp) if status_dp else None,
        knx_index,
    )
    acc.states["current_position"] = _state(
        opts, floor, room, label, "current_position", "number", "FROM_OBS"
    )
    acc.states["target_position"] = _state(
        opts, floor, room, label, "target_position", "number", "BOTH"
    )
    acc.states["position_state"] = _state(
        opts,
        floor,
        room,
        label,
        "position_state",
        "number",
        "FROM_OBS",
        initial_value=2,
        notes=["Phase-1-Default ohne Bewegungsrueckmeldung: 2 = stopped."],
    )
    if cfg.get("dp_stop"):
        acc.states["stop"] = _state(
            opts,
            floor,
            room,
            label,
            "stop",
            "boolean",
            "BOTH",
            notes=["Interner Fallback-State, kein Yahka-Mapping."],
        )
        acc.obs.extra["stop_dp_id"] = cfg.get("dp_stop")
    acc.warnings.append("Rolladensteuerung vor Freigabe pro Aktor einzeln pruefen.")
    return acc


def _rtr_accessory(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
) -> HomeKitAccessorySpec:
    label = _display_label(widget, "Heizung")
    cfg = widget.get("config") or {}
    acc = HomeKitAccessorySpec(
        name_key=slugify(label),
        apple_home_name=label,
        type="Thermostat",
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
    )
    acc.states["current_temperature"] = _state(
        opts, floor, room, label, "current_temperature", "number", "FROM_OBS"
    )
    acc.states["target_temperature"] = _state(
        opts,
        floor,
        room,
        label,
        "target_temperature",
        "number",
        "BOTH",
        notes=["Clamp fuer HomeKit: 10..38 Grad Celsius."],
    )
    acc.states["current_heating_cooling_state"] = _state(
        opts,
        floor,
        room,
        label,
        "current_heating_cooling_state",
        "number",
        "FROM_OBS",
        notes=["Aus KNX DPT1.100 Heizbedarf oder heating_active ableiten."],
    )
    acc.states["target_heating_cooling_state"] = _state(
        opts,
        floor,
        room,
        label,
        "target_heating_cooling_state",
        "number",
        "BOTH",
        notes=["DPT20/HVAC-Betriebsmodus auf HomeKit-Zielmodus abbilden."],
    )
    acc.obs.switch_dp_id = str(widget.get("datapoint_id") or "") or None
    acc.obs.status_dp_id = str(cfg.get("actual_temp_dp_id") or "") or None
    acc.obs.extra.update(
        {
            "actual_temp_dp_id": cfg.get("actual_temp_dp_id"),
            "knx_hvac_mode_dp_id": cfg.get("mode_dp_id"),
            "target_temperature_range": [10, 38],
            "target_temperature_step": cfg.get("step", 0.5),
        }
    )
    if not cfg.get("actual_temp_dp_id"):
        acc.warnings.append(
            "Kein Isttemperatur-DP erkannt: ggf. nur TemperatureSensor anlegen."
        )
    acc.warnings.append("Sollwert-Roundtrip/Rundung pro RTR testen und dokumentieren.")
    return acc


def _value_sensor_accessory(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
    dp_index: dict[str, dict[str, Any]],
) -> HomeKitAccessorySpec | None:
    dp = _dp_id(widget)
    if not dp:
        return None
    dp_info = dp_index.get(dp, {})
    unit = (dp_info.get("unit") or "").lower()
    name = _display_label(widget, dp_info.get("name") or "Sensor")
    haystack = f"{name} {unit} {' '.join(dp_info.get('tags') or [])}".lower()
    if "°c" in haystack or "temp" in haystack:
        typ = "TemperatureSensor"
        prop = "current_temperature"
    elif "%" in haystack and ("feuchte" in haystack or "humid" in haystack):
        typ = "HumiditySensor"
        prop = "current_relative_humidity"
    else:
        return None
    acc = HomeKitAccessorySpec(
        name_key=slugify(name),
        apple_home_name=name,
        type=typ,
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
    )
    acc.states[prop] = _state(opts, floor, room, name, prop, "number", "FROM_OBS")
    acc.obs.status_dp_id = dp
    acc.obs.binding_direction = "FROM_OBS"
    return acc


def _unsupported_accessory(
    widget: dict[str, Any], floor: str, room: str
) -> HomeKitAccessorySpec:
    label = _display_label(widget, widget.get("type") or "Widget")
    return HomeKitAccessorySpec(
        name_key=slugify(label),
        apple_home_name=label,
        type="Unsupported",
        widget_id=widget.get("id"),
        widget_type=widget.get("type"),
        warnings=[
            f"Widget-Typ {widget.get('type')!r} wird nicht automatisch nach HomeKit gemappt."
        ],
    )


def _accessories_for_widget(
    widget: dict[str, Any],
    floor: str,
    room: str,
    opts: HomeKitPreviewOptions,
    dp_index: dict[str, dict[str, Any]],
    knx_index: dict[str, dict[str, Any]],
) -> list[HomeKitAccessorySpec]:
    typ = widget.get("type")
    if typ == "Licht":
        return [_light_accessory(widget, floor, room, opts, knx_index)]
    if typ == "Toggle":
        return [_toggle_accessory(widget, floor, room, opts, knx_index, dp_index)]
    if typ == "Fenster":
        return _contact_accessories(widget, floor, room, opts)
    if typ == "Rolladen":
        return [_rolladen_accessory(widget, floor, room, opts, knx_index)]
    if typ == "RTR":
        return [_rtr_accessory(widget, floor, room, opts)]
    if typ == "ValueDisplay":
        sensor = _value_sensor_accessory(widget, floor, room, opts, dp_index)
        return [sensor] if sensor else [_unsupported_accessory(widget, floor, room)]
    return [_unsupported_accessory(widget, floor, room)]


def _summarize(rooms: list[HomeKitRoomSpec], limit: int) -> HomeKitPreviewSummary:
    summary = HomeKitPreviewSummary(rooms=len(rooms))
    for room in rooms:
        for acc in room.accessories:
            summary.accessories += 1
            if acc.warnings:
                summary.needs_manual_review += 1
            match acc.type:
                case "Lightbulb":
                    summary.lightbulb += 1
                case "Outlet":
                    summary.outlet += 1
                case "Switch":
                    summary.switch += 1
                case "ContactSensor":
                    summary.contact_sensor += 1
                case "WindowCovering":
                    summary.window_covering += 1
                case "Thermostat":
                    summary.thermostat += 1
                case "TemperatureSensor":
                    summary.temperature_sensor += 1
                case "HumiditySensor":
                    summary.humidity_sensor += 1
                case _:
                    summary.unsupported += 1
    summary.exceeds_single_bridge_limit = summary.accessories > limit
    summary.recommended_bridges = max(1, (summary.accessories + limit - 1) // limit)
    return summary


def _system_states() -> list[HomeKitSystemStateSpec]:
    return [
        HomeKitSystemStateSpec(
            id="0_userdata.0.obs.system.online",
            data_type="boolean",
            writer="OBS",
            notes=["Heartbeat: true solange OBS laeuft."],
        ),
        HomeKitSystemStateSpec(
            id="0_userdata.0.obs.system.last_seen",
            data_type="string",
            writer="OBS",
            notes=[
                "Heartbeat-Timestamp, Fallback nach 30..60 Sekunden ohne Aktualisierung."
            ],
        ),
        HomeKitSystemStateSpec(
            id="0_userdata.0.obs.system.iobroker_adapter_connected",
            data_type="boolean",
            writer="ioBroker Script",
            source="system.adapter.obs.0.connected",
            notes=["Instanznummer an reale OBS/ioBroker-Adapterinstanz anpassen."],
        ),
        HomeKitSystemStateSpec(
            id="0_userdata.0.obs.system.fallback_active",
            data_type="boolean",
            writer="ioBroker Script",
            notes=[
                "Nur aktiv, wenn Heartbeat und Adapter-Gesundheit Ausfall signalisieren."
            ],
        ),
    ]


def _backup_items() -> list[str]:
    return [
        "HomeKit/Yahka-Mapping-Datei in einem versionierten Backup-Pfad sichern",
        "ioBroker 0_userdata.0.obs.* States persistent sichern",
        "Yahka-Konfiguration",
        "Yahka-Pairing-Datenpfad der Installation sichern",
        "ioBroker-Scripts fuer Heartbeat, Adapterstatus und Fallback sichern",
        "KNX-/Fallback-Pfad separat pruefen und dokumentieren",
    ]


def _obs_data_type(data_type: str) -> str:
    return {
        "boolean": "BOOLEAN",
        "bool": "BOOLEAN",
        "number": "FLOAT",
        "float": "FLOAT",
        "integer": "INTEGER",
        "int": "INTEGER",
        "string": "STRING",
    }.get(data_type.lower(), "STRING")


def _source_data_type(data_type: str) -> str | None:
    return {
        "BOOLEAN": "bool",
        "FLOAT": "float",
        "INTEGER": "int",
        "STRING": "string",
    }.get(_obs_data_type(data_type))


def _state_role(accessory_type: str, state_key: str) -> str:
    if state_key == "on":
        return "switch"
    if accessory_type == "ContactSensor":
        return "sensor.window"
    if "temperature" in state_key:
        return "level.temperature"
    if "position" in state_key:
        return "level.blind"
    if state_key == "position_state":
        return "indicator.state"
    return "state"


def _restore_tests() -> list[str]:
    return [
        "Yahka-Daten zurueckspielen und pruefen, dass Apple Home die Bridge ohne neues Pairing erkennt",
        "Mapping-Datei und 0_userdata.0.obs.* States wiederherstellen",
        "OBS starten und Re-Sync der ioBroker Home-States pruefen",
        "Szenen und Automationen in Apple Home stichprobenartig pruefen",
    ]


async def build_preview(db: Database, opts: HomeKitPreviewOptions) -> HomeKitPreview:
    rows = await _load_visu_rows(db)
    root = _find_root(rows, opts)
    if root is None:
        raise ValueError(
            f"VISU root not found: {opts.root_node_id or opts.source_visu_name}"
        )

    dp_index = await _load_dp_index(db)
    knx_index = await _load_knx_index(db)
    by_id = {row["id"]: row for row in rows}
    descendants = _descendants(rows, root["id"])
    rooms_by_key: dict[tuple[str, str], HomeKitRoomSpec] = {}
    warnings: list[str] = []

    for node in descendants:
        if node.get("type") != "PAGE":
            continue
        widgets = _page_widgets(node)
        if not widgets:
            continue
        path = _path_for(node, by_id, root["id"])
        floor = path[0]["name"] if len(path) > 1 else "Allgemein"
        room = path[-1]["name"]
        room_key = slugify(room)
        apple_room = f"{floor} {room}" if opts.room_strategy == "floor_prefix" else room
        key = (floor, room)
        room_spec = rooms_by_key.setdefault(
            key,
            HomeKitRoomSpec(
                floor=floor,
                room_key=room_key,
                room_display_name=room,
                apple_home_room=apple_room,
            ),
        )
        for widget in widgets:
            room_spec.accessories.extend(
                _accessories_for_widget(widget, floor, room, opts, dp_index, knx_index)
            )

    rooms = list(rooms_by_key.values())
    rooms.sort(key=lambda r: (r.floor, r.room_display_name))
    summary = _summarize(rooms, opts.accessory_limit_per_bridge)
    if summary.exceeds_single_bridge_limit:
        warnings.append(
            f"HomeKit-Bridge-Limit ueberschritten: {summary.accessories} Accessories, "
            f"{summary.recommended_bridges} Yahka-Bridges empfohlen."
        )

    return HomeKitPreview(
        project=opts.project,
        source_visu=root["name"],
        leading_iobroker={
            "name": opts.leading_iobroker_name,
            "host": opts.leading_iobroker_host,
            "port": opts.leading_iobroker_port,
            "role": "yahka_and_fallback",
        },
        room_strategy=opts.room_strategy,
        accessory_limit_per_bridge=opts.accessory_limit_per_bridge,
        rooms=rooms,
        summary=summary,
        warnings=warnings,
        system_states=_system_states(),
        backup_items=_backup_items(),
        restore_tests=_restore_tests(),
    )


async def _existing_iobroker_bindings(
    db: Database, instance_id: str
) -> dict[str, tuple[str, str]]:
    rows = await db.fetchall(
        "SELECT id, datapoint_id, config FROM adapter_bindings WHERE adapter_instance_id=?",
        (instance_id,),
    )
    result: dict[str, tuple[str, str]] = {}
    for row in rows:
        try:
            cfg = json.loads(row["config"] or "{}")
        except Exception:
            continue
        state_id = cfg.get("state_id")
        if state_id:
            result[str(state_id)] = (row["datapoint_id"], row["id"])
    return result


async def _create_iobroker_binding(
    db: Database,
    dp_id: str,
    instance_id: str,
    direction: str,
    state: HomeKitStateSpec,
) -> str:
    row = await db.fetchone(
        "SELECT adapter_type FROM adapter_instances WHERE id=?", (instance_id,)
    )
    if row is None:
        raise ValueError(f"ioBroker instance not found: {instance_id}")
    now = datetime.now(timezone.utc).isoformat()
    binding_id = str(uuid.uuid4())
    config: dict[str, Any] = {
        "state_id": state.id,
        "ack": True,
    }
    source_type = _source_data_type(state.data_type)
    if source_type:
        config["source_data_type"] = source_type
    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
           (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            binding_id,
            dp_id,
            row["adapter_type"],
            instance_id,
            direction,
            json.dumps(config),
            now,
            now,
        ),
    )
    return binding_id


def _target_datapoint_for_state(
    accessory: HomeKitAccessorySpec, state_key: str
) -> str | None:
    """Return an existing OBS datapoint that should carry this HomeKit state.

    The preferred rollout model is to attach Yahka/ioBroker states to the
    existing KNX/ETS datapoint instead of creating a parallel HomeKit datapoint.
    Only helper states without a KNX counterpart should remain standalone.
    """
    if state_key == "on":
        return accessory.obs.switch_dp_id or accessory.obs.status_dp_id
    if state_key in {
        "contact",
        "current_temperature",
        "current_relative_humidity",
        "current_position",
        "current_heating_cooling_state",
    }:
        return accessory.obs.status_dp_id or accessory.obs.switch_dp_id
    if state_key in {
        "target_position",
        "target_temperature",
        "target_heating_cooling_state",
    }:
        return accessory.obs.switch_dp_id
    if state_key == "stop":
        value = accessory.obs.extra.get("stop_dp_id")
        return str(value) if value else None
    return None


async def _datapoint_exists(db: Database, dp_id: str | None) -> bool:
    if not dp_id:
        return False
    row = await db.fetchone("SELECT id FROM datapoints WHERE id=?", (dp_id,))
    return row is not None


async def _merge_datapoint_metadata(
    db: Database,
    dp_id: str,
    data_type: str,
    tags: list[str],
) -> None:
    row = await db.fetchone(
        "SELECT tags, data_type FROM datapoints WHERE id=?", (dp_id,)
    )
    if row is None:
        return
    try:
        current_tags = json.loads(row["tags"] or "[]")
    except Exception:
        current_tags = []
    merged_tags: list[str] = []
    for tag in [*current_tags, *tags]:
        if tag and tag not in merged_tags:
            merged_tags.append(tag)
    await db.execute_and_commit(
        "UPDATE datapoints SET data_type=?, tags=?, updated_at=? WHERE id=?",
        (
            data_type if row["data_type"] == "UNKNOWN" else row["data_type"],
            json.dumps(merged_tags),
            datetime.now(timezone.utc).isoformat(),
            dp_id,
        ),
    )


async def _set_knx_binding_direction(
    db: Database,
    dp_id: str | None,
    direction: str,
) -> None:
    if not dp_id:
        return
    await db.execute_and_commit(
        """UPDATE adapter_bindings
           SET direction=?, updated_at=?
           WHERE datapoint_id=? AND adapter_type='KNX'""",
        (direction, datetime.now(timezone.utc).isoformat(), dp_id),
    )


async def _normalize_knx_directions_for_accessory(
    db: Database, accessory: HomeKitAccessorySpec
) -> None:
    if accessory.obs.switch_dp_id:
        await _set_knx_binding_direction(db, accessory.obs.switch_dp_id, "DEST")
    if (
        accessory.obs.status_dp_id
        and accessory.obs.status_dp_id != accessory.obs.switch_dp_id
    ):
        await _set_knx_binding_direction(db, accessory.obs.status_dp_id, "SOURCE")


async def _ensure_iobroker_state(
    instance_id: str,
    accessory: HomeKitAccessorySpec,
    state_key: str,
    state: HomeKitStateSpec,
) -> bool:
    from obs.adapters import registry as adapter_registry

    instance = adapter_registry.get_instance_by_id(instance_id)
    if instance is None or not getattr(instance, "connected", False):
        raise RuntimeError("ioBroker-Instanz ist nicht verbunden")
    if not hasattr(instance, "ensure_state"):
        raise RuntimeError("ioBroker-Adapter kann States nicht anlegen")

    await instance.ensure_state(
        {
            "state_id": state.id,
            "data_type": _obs_data_type(state.data_type),
            "name": f"{accessory.apple_home_name} {state_key}",
            "role": _state_role(accessory.type, state_key),
            "read": True,
            "write": state.binding_direction == "BOTH",
            "initial_value": state.initial_value,
        }
    )
    return True


async def apply_mapping(
    db: Database, registry: Any, request: HomeKitApplyRequest
) -> HomeKitApplyResult:
    """Create OBS datapoints and ioBroker bindings from a preview.

    The function is idempotent by ioBroker state_id: existing bindings for the
    selected ioBroker instance are skipped instead of duplicated.
    """
    instance_id = str(request.iobroker_instance_id)
    instance_row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (instance_id,)
    )
    if instance_row is None:
        raise ValueError(f"ioBroker instance not found: {instance_id}")
    if instance_row["adapter_type"] != "IOBROKER":
        raise ValueError("HomeKit apply requires an IOBROKER adapter instance")

    preview = await build_preview(db, request)
    existing = await _existing_iobroker_bindings(db, instance_id)
    selected_rooms = set(request.room_keys)
    result = HomeKitApplyResult(
        dry_run=request.dry_run, preview_summary=preview.summary
    )
    selected_rooms_for_summary: list[HomeKitRoomSpec] = []

    for room in preview.rooms:
        if (
            selected_rooms
            and room.room_key not in selected_rooms
            and room.apple_home_room not in selected_rooms
        ):
            continue
        selected_room = room.model_copy(update={"accessories": []})
        selected_rooms_for_summary.append(selected_room)
        for accessory in room.accessories:
            selected_room.accessories.append(accessory)
            if accessory.type == "Unsupported" and not request.include_unsupported:
                result.skipped_unsupported += 1
                result.items.append(
                    HomeKitApplyItem(
                        state_id="",
                        room=room.apple_home_room,
                        accessory=accessory.apple_home_name,
                        accessory_type=accessory.type,
                        state_key="",
                        data_type="",
                        binding_direction="FROM_OBS",
                        obs_binding_direction="DEST",
                        action="skip_unsupported",
                        message="Unsupported widget is not imported automatically.",
                    )
                )
                continue
            for state_key, state in accessory.states.items():
                if state.id in existing:
                    dp_id, binding_id = existing[state.id]
                    iobroker_state_created = False
                    message = None
                    if request.create_iobroker_states and not request.dry_run:
                        try:
                            iobroker_state_created = await _ensure_iobroker_state(
                                instance_id,
                                accessory,
                                state_key,
                                state,
                            )
                            result.created_iobroker_states += 1
                        except Exception as exc:
                            message = str(exc)
                            result.errors.append(f"{state.id}: {exc}")
                    result.skipped_existing += 1
                    result.items.append(
                        HomeKitApplyItem(
                            state_id=state.id,
                            room=room.apple_home_room,
                            accessory=accessory.apple_home_name,
                            accessory_type=accessory.type,
                            state_key=state_key,
                            data_type=_obs_data_type(state.data_type),
                            binding_direction=state.binding_direction,
                            obs_binding_direction=state.obs_binding_direction,
                            action="skip_existing",
                            datapoint_id=dp_id,
                            binding_id=binding_id,
                            iobroker_state_created=iobroker_state_created,
                            message=message,
                        )
                    )
                    continue

                datapoint_tags = [
                    "homekit",
                    "yahka",
                    "obs-home",
                    slugify(request.project),
                    slugify(room.floor),
                    room.room_key,
                    slugify(accessory.type),
                ]
                target_dp_id = _target_datapoint_for_state(accessory, state_key)
                reuse_existing_dp = await _datapoint_exists(db, target_dp_id)
                item = HomeKitApplyItem(
                    state_id=state.id,
                    room=room.apple_home_room,
                    accessory=accessory.apple_home_name,
                    accessory_type=accessory.type,
                    state_key=state_key,
                    data_type=_obs_data_type(state.data_type),
                    binding_direction=state.binding_direction,
                    obs_binding_direction=state.obs_binding_direction,
                    action="reuse_existing" if reuse_existing_dp else "create",
                    datapoint_id=target_dp_id if reuse_existing_dp else None,
                    message="Bestehendes KNX/ETS-OBS-Objekt wird wiederverwendet."
                    if reuse_existing_dp
                    else None,
                )
                if request.dry_run:
                    result.items.append(item)
                    continue

                try:
                    if reuse_existing_dp and target_dp_id:
                        await _merge_datapoint_metadata(
                            db,
                            target_dp_id,
                            _obs_data_type(state.data_type),
                            datapoint_tags,
                        )
                        await _normalize_knx_directions_for_accessory(db, accessory)
                        dp_id = target_dp_id
                    else:
                        from obs.models.datapoint import DataPointCreate

                        dp = await registry.create(
                            DataPointCreate(
                                name=f"{room.apple_home_room} {accessory.apple_home_name} {state_key}",
                                data_type=_obs_data_type(state.data_type),
                                tags=datapoint_tags,
                                persist_value=state.persistent,
                                record_history=False,
                            )
                        )
                        dp_id = str(dp.id)
                        item.datapoint_id = dp_id
                        result.created_datapoints += 1

                    item.binding_id = await _create_iobroker_binding(
                        db,
                        dp_id,
                        instance_id,
                        state.obs_binding_direction,
                        state,
                    )
                    result.created_bindings += 1
                    existing[state.id] = (dp_id, item.binding_id)

                    if request.create_iobroker_states:
                        item.iobroker_state_created = await _ensure_iobroker_state(
                            instance_id,
                            accessory,
                            state_key,
                            state,
                        )
                        result.created_iobroker_states += 1
                except Exception as exc:
                    item.action = "error"
                    item.message = str(exc)
                    result.errors.append(f"{state.id}: {exc}")
                result.items.append(item)

    if not request.dry_run:
        from obs.adapters.registry import reload_instance_bindings

        await reload_instance_bindings(instance_id, db)

    result.apply_summary = _summarize(
        selected_rooms_for_summary, request.accessory_limit_per_bridge
    )
    return result
