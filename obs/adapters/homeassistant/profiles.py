"""Home Assistant binding model, domain profiles and value/action resolution (#1392).

Everything in this module is pure and declarative: no I/O, no executable
expressions.  The adapter, the import API and the tests share it.

Binding resolution, in order of precedence:

1. **legacy** — any of ``service_domain`` / ``service_name`` / ``service_data_key``
   is set.  Writes behave exactly like before #1392, even when a profile is set.
2. **write_actions** — value-dependent dispatch, keyed by the typed *logical*
   OBS value (``"true"`` / ``"false"`` for Booleans, the literal for enums).
3. **action** — one action, the transformed value goes to ``value_path``.
4. **profile** — the actions of an explicitly chosen profile.
5. **default** — no write configuration at all: the pre-#1392 default
   (``turn_on``/``turn_off`` for Booleans, ``set_value`` otherwise).

Reads use the binding's ``attribute``/``state_map``/``transitional_states``/
``fault_states`` and fall back to the profile's values for those not set.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from obs.core.json import jsonable

# ---------------------------------------------------------------------------
# Binding configuration
# ---------------------------------------------------------------------------

_IDENT = r"^[a-z0-9_]+$"
ENTITY_ID_PATTERN = r"^[a-z0-9_]+\.[a-z0-9_]+$"
_ENTITY_ID = re.compile(ENTITY_ID_PATTERN)
_PATH = r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"
_TARGET_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id"})

Scalar = str | int | float | bool | None
ActionValue = Scalar | list[Scalar]


class HaAction(BaseModel):
    """One declarative Home Assistant action call."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(pattern=_IDENT, max_length=64)
    name: str = Field(pattern=_IDENT, max_length=64)
    # Default: {"entity_id": <binding entity_id>}
    target: dict[str, str | list[str]] | None = None
    data: dict[str, ActionValue] = Field(default_factory=dict, max_length=32)
    # Field (dot path) that receives the transformed OBS value; None = no value.
    value_path: str | None = Field(default=None, pattern=_PATH, max_length=128)

    @field_validator("target")
    @classmethod
    def _check_target(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            unknown = set(value) - _TARGET_KEYS
            if unknown:
                raise ValueError(f"unsupported target keys: {sorted(unknown)}")
        return value

    @field_validator("data")
    @classmethod
    def _check_data_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not re.match(_PATH, key):
                raise ValueError(f"invalid data key: {key!r}")
        return value


class HaBindingConfig(BaseModel):
    entity_id: str  # e.g. "light.living_room"
    attribute: str | None = None  # None → state field; "brightness" → attribute
    # Legacy write fields (pre-#1392) — take precedence over everything below.
    service_domain: str | None = None  # override: e.g. "homeassistant"
    service_name: str | None = None  # override: e.g. "toggle", "set_value"
    service_data_key: str | None = None  # numeric/string write key, e.g. "value", "brightness"
    # Declarative mapping (#1392)
    profile: str | None = None
    action: HaAction | None = None
    write_actions: dict[str, HaAction] | None = Field(default=None, max_length=64)
    state_map: dict[str, Scalar] | None = Field(default=None, max_length=64)
    transitional_states: list[str] = Field(default_factory=list, max_length=32)
    fault_states: list[str] = Field(default_factory=list, max_length=32)
    # How the read value is typed; None = from the profile, else the legacy coercion.
    read_as: Literal["auto", "boolean", "number", "string"] | None = None
    # Stable import identity: hash of the HA registry unique_id (never the raw id).
    ha_uid: str | None = Field(default=None, pattern=r"^[0-9a-f]{16,64}$")

    @field_validator("entity_id")
    @classmethod
    def _check_entity_id(cls, value: str) -> str:
        # HA rejects anything else in subscribe_entities for the whole request,
        # so one malformed binding must never reach the subscription. HA itself
        # lowercases entity ids in actions, so "Light.Kitchen" stays valid.
        value = value.strip().lower()
        if not _ENTITY_ID.match(value):
            raise ValueError(f"invalid entity_id {value!r} (expected <domain>.<object_id>, lowercase a-z, 0-9, _)")
        return value

    @model_validator(mode="after")
    def _check_mapping(self) -> HaBindingConfig:
        if self.action is not None and self.write_actions is not None:
            raise ValueError("'action' and 'write_actions' are mutually exclusive")
        if uses_legacy_write(self) and (self.action is not None or self.write_actions is not None):
            raise ValueError("legacy service_* fields cannot be combined with 'action'/'write_actions'")
        if self.profile is not None:
            profile = PROFILES.get(self.profile)
            if profile is None:
                raise ValueError(f"unknown profile: {self.profile!r}")
            if entity_domain(self.entity_id) not in profile.domains:
                raise ValueError(f"profile {self.profile!r} does not apply to {self.entity_id!r}")
        return self


def direction_problem(bc: HaBindingConfig, direction: str) -> str | None:
    """Why *bc* cannot work in *direction*, or None.

    A read-only profile has nothing to write and a command profile nothing to
    read, unless the binding supplies that side explicitly.
    """
    profile = PROFILES.get(bc.profile or "")
    if profile is None:
        return None
    writes = direction in ("DEST", "BOTH")
    reads = direction in ("SOURCE", "BOTH")
    explicit_write = uses_legacy_write(bc) or bc.action is not None or bc.write_actions is not None
    explicit_read = bool(bc.attribute) or bc.state_map is not None
    if writes and profile.direction == "SOURCE" and not explicit_write:
        return f"Profil {profile.id} ist nur lesend und kann nicht schreiben ({direction})"
    if reads and profile.direction == "DEST" and not explicit_read:
        return f"Profil {profile.id} ist ein reiner Befehl und kann nicht lesen ({direction})"
    return None


def uses_legacy_write(bc: HaBindingConfig) -> bool:
    return bool(bc.service_domain or bc.service_name or bc.service_data_key)


def entity_domain(entity_id: str) -> str:
    """Extract domain from entity_id (part before first '.')."""
    return entity_id.split(".", maxsplit=1)[0]


def identity_hash(platform: str | None, unique_id: str | None) -> str | None:
    """Stable, non-reversible identity for an HA registry entry."""
    if not unique_id:
        return None
    return hashlib.sha256(f"{platform or ''}:{unique_id}".encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileAction:
    name: str
    value_path: str | None = None
    domain: str | None = None  # None → entity domain


@dataclass(frozen=True)
class HaProfile:
    id: str
    domains: tuple[str, ...]
    # AUTO: inferred from the entity state at import; read with the legacy coercion
    data_type: Literal["AUTO", "BOOLEAN", "FLOAT", "INTEGER", "STRING"]
    direction: Literal["SOURCE", "DEST", "BOTH"]
    attribute: str | None = None
    state_map: dict[str, Any] | None = None
    transitional_states: tuple[str, ...] = ()
    fault_states: tuple[str, ...] = ()
    action: ProfileAction | None = None
    write_actions: dict[str, ProfileAction] | None = None
    # supported_features bits the entity must advertise (when it advertises any)
    required_features: int = 0
    unit: str | None = None
    # i18n key suffix for the Boolean/command convention shown in previews
    convention: str | None = None
    # proposed automatically for its domain(s) in the import browser
    default: bool = False
    # Ambiguous operation: never proposed automatically, only on explicit choice
    explicit_only: bool = False
    # Only offered when this condition on the entity holds
    when: str | None = None  # "position" → current_position attribute present
    # Not offered when an attribute has exactly this value (e.g. an on/off-only light)
    excluded_if: dict[str, Any] = field(default_factory=dict)
    # Value of a missing attribute in a given entity state (e.g. brightness 0 while off)
    missing_values: dict[str, Any] = field(default_factory=dict)


_ON_OFF = {"on": True, "off": False}
_OPEN_CLOSED = {"open": True, "closed": False}
_MOTION = ("opening", "closing")

# Feature bits from the HA entity integrations
COVER_OPEN, COVER_CLOSE, COVER_SET_POSITION, COVER_STOP, COVER_SET_TILT = 1, 2, 4, 8, 128
VALVE_OPEN, VALVE_CLOSE, VALVE_SET_POSITION, VALVE_STOP = 1, 2, 4, 8
LOCK_OPEN = 1
MP_PAUSE, MP_VOLUME_SET, MP_VOLUME_MUTE, MP_PREVIOUS, MP_NEXT = 1, 4, 8, 16, 32
MP_TURN_ON, MP_TURN_OFF, MP_SELECT_SOURCE, MP_STOP, MP_PLAY = 128, 256, 2048, 4096, 16384

_PROFILE_LIST: tuple[HaProfile, ...] = (
    HaProfile("sensor.value", ("sensor",), "AUTO", "SOURCE", default=True),
    HaProfile("binary_sensor.state", ("binary_sensor",), "BOOLEAN", "SOURCE", state_map=_ON_OFF, default=True, convention="trueOn"),
    HaProfile(
        "switch.on_off",
        ("switch", "input_boolean", "fan"),
        "BOOLEAN",
        "BOTH",
        state_map=_ON_OFF,
        write_actions={"true": ProfileAction("turn_on"), "false": ProfileAction("turn_off")},
        default=True,
        convention="trueOn",
    ),
    HaProfile(
        "light.on_off",
        ("light",),
        "BOOLEAN",
        "BOTH",
        state_map=_ON_OFF,
        write_actions={"true": ProfileAction("turn_on"), "false": ProfileAction("turn_off")},
        default=True,
        convention="trueOn",
    ),
    HaProfile(
        "light.brightness",
        ("light",),
        "INTEGER",
        "BOTH",
        attribute="brightness",
        action=ProfileAction("turn_on", value_path="brightness"),
        # HA drops the brightness attribute while a light is off: that is brightness 0.
        missing_values={"off": 0},
        excluded_if={"supported_color_modes": ["onoff"]},
    ),
    HaProfile(
        "lock.boolean_locked",
        ("lock",),
        "BOOLEAN",
        "BOTH",
        state_map={"locked": True, "unlocked": False, "open": False},
        transitional_states=("locking", "unlocking", "opening"),
        fault_states=("jammed",),
        write_actions={"true": ProfileAction("lock"), "false": ProfileAction("unlock")},
        default=True,
        convention="trueLocked",
    ),
    HaProfile(
        "lock.open",
        ("lock",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("open")},
        required_features=LOCK_OPEN,
        convention="commandOnTrue",
    ),
    HaProfile(
        "cover.boolean_open",
        ("cover",),
        "BOOLEAN",
        "BOTH",
        state_map=_OPEN_CLOSED,
        transitional_states=_MOTION,
        write_actions={"true": ProfileAction("open_cover"), "false": ProfileAction("close_cover")},
        required_features=COVER_OPEN | COVER_CLOSE,
        default=True,
        convention="trueOpen",
    ),
    HaProfile(
        "cover.position",
        ("cover",),
        "INTEGER",
        "BOTH",
        attribute="current_position",
        action=ProfileAction("set_cover_position", value_path="position"),
        required_features=COVER_SET_POSITION,
        unit="%",
        default=True,
        when="current_position",
    ),
    HaProfile(
        "cover.tilt_position",
        ("cover",),
        "INTEGER",
        "BOTH",
        attribute="current_tilt_position",
        action=ProfileAction("set_cover_tilt_position", value_path="tilt_position"),
        required_features=COVER_SET_TILT,
        unit="%",
        when="current_tilt_position",
    ),
    HaProfile(
        "cover.stop",
        ("cover",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("stop_cover")},
        required_features=COVER_STOP,
        convention="commandOnTrue",
    ),
    HaProfile(
        "valve.boolean_open",
        ("valve",),
        "BOOLEAN",
        "BOTH",
        state_map=_OPEN_CLOSED,
        transitional_states=_MOTION,
        write_actions={"true": ProfileAction("open_valve"), "false": ProfileAction("close_valve")},
        required_features=VALVE_OPEN | VALVE_CLOSE,
        default=True,
        convention="trueOpen",
    ),
    HaProfile(
        "valve.position",
        ("valve",),
        "INTEGER",
        "BOTH",
        attribute="current_position",
        action=ProfileAction("set_valve_position", value_path="position"),
        required_features=VALVE_SET_POSITION,
        unit="%",
        default=True,
        when="current_position",
    ),
    HaProfile(
        "valve.stop",
        ("valve",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("stop_valve")},
        required_features=VALVE_STOP,
        convention="commandOnTrue",
    ),
    HaProfile("media_player.state", ("media_player",), "STRING", "SOURCE", default=True),
    HaProfile(
        "media_player.play_pause",
        ("media_player",),
        "BOOLEAN",
        "BOTH",
        state_map={"playing": True, "paused": False, "idle": False, "off": False, "standby": False, "on": False},
        transitional_states=("buffering",),
        write_actions={"true": ProfileAction("media_play"), "false": ProfileAction("media_pause")},
        required_features=MP_PLAY | MP_PAUSE,
        explicit_only=True,
        convention="truePlaying",
    ),
    HaProfile(
        "media_player.power",
        ("media_player",),
        "BOOLEAN",
        "BOTH",
        state_map={"off": False, "standby": False, "on": True, "idle": True, "playing": True, "paused": True, "buffering": True},
        write_actions={"true": ProfileAction("turn_on"), "false": ProfileAction("turn_off")},
        required_features=MP_TURN_ON | MP_TURN_OFF,
        explicit_only=True,
        convention="trueOn",
    ),
    HaProfile(
        "media_player.stop",
        ("media_player",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("media_stop")},
        required_features=MP_STOP,
        convention="commandOnTrue",
    ),
    HaProfile(
        "media_player.next",
        ("media_player",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("media_next_track")},
        required_features=MP_NEXT,
        convention="commandOnTrue",
    ),
    HaProfile(
        "media_player.previous",
        ("media_player",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("media_previous_track")},
        required_features=MP_PREVIOUS,
        convention="commandOnTrue",
    ),
    HaProfile(
        "media_player.volume",
        ("media_player",),
        "FLOAT",
        "BOTH",
        attribute="volume_level",
        action=ProfileAction("volume_set", value_path="volume_level"),
        required_features=MP_VOLUME_SET,
        convention="volumeFraction",
    ),
    HaProfile(
        "media_player.mute",
        ("media_player",),
        "BOOLEAN",
        "BOTH",
        attribute="is_volume_muted",
        action=ProfileAction("volume_mute", value_path="is_volume_muted"),
        required_features=MP_VOLUME_MUTE,
        convention="trueMuted",
    ),
    HaProfile(
        "media_player.source",
        ("media_player",),
        "STRING",
        "BOTH",
        attribute="source",
        action=ProfileAction("select_source", value_path="source"),
        required_features=MP_SELECT_SOURCE,
    ),
    HaProfile("media_player.title", ("media_player",), "STRING", "SOURCE", attribute="media_title"),
    HaProfile("media_player.artist", ("media_player",), "STRING", "SOURCE", attribute="media_artist"),
    HaProfile(
        "number.value",
        ("number", "input_number"),
        "FLOAT",
        "BOTH",
        action=ProfileAction("set_value", value_path="value"),
        default=True,
    ),
    HaProfile(
        "select.option",
        ("select", "input_select"),
        "STRING",
        "BOTH",
        action=ProfileAction("select_option", value_path="option"),
        default=True,
    ),
    HaProfile(
        "text.value",
        ("text", "input_text"),
        "STRING",
        "BOTH",
        action=ProfileAction("set_value", value_path="value"),
        default=True,
    ),
    HaProfile(
        "button.press",
        ("button", "input_button"),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("press")},
        default=True,
        convention="commandOnTrue",
    ),
    HaProfile(
        "scene.activate",
        ("scene",),
        "BOOLEAN",
        "DEST",
        write_actions={"true": ProfileAction("turn_on")},
        default=True,
        convention="commandOnTrue",
    ),
)

PROFILES: dict[str, HaProfile] = {p.id: p for p in _PROFILE_LIST}

# Domains whose operations are ambiguous: the import never infers a write
# mapping for them; the user must pick a profile explicitly (or none exists yet).
AMBIGUOUS_DOMAINS = frozenset(
    {"alarm_control_panel", "climate", "media_player", "vacuum", "lawn_mower", "water_heater", "humidifier", "remote"},
)


def profiles_for_domain(domain: str) -> list[HaProfile]:
    return [p for p in _PROFILE_LIST if domain in p.domains]


def profile_supported(profile: HaProfile, state_obj: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Whether an entity advertises what *profile* needs. Returns (ok, reason_code)."""
    attrs = (state_obj or {}).get("attributes") or {}
    features = attrs.get("supported_features")
    if profile.required_features and isinstance(features, int) and features & profile.required_features != profile.required_features:
        return False, "featureMissing"
    if profile.when and state_obj is not None and profile.when not in attrs:
        return False, "attributeMissing"
    if any(attrs.get(key) == value for key, value in profile.excluded_if.items()):
        return False, "featureMissing"
    return True, None


def default_profile(domain: str, state_obj: dict[str, Any] | None) -> HaProfile | None:
    """The profile proposed automatically for an entity, or None."""
    for profile in profiles_for_domain(domain):
        if profile.default and not profile.explicit_only and profile_supported(profile, state_obj)[0]:
            return profile
    return None


def profile_catalog() -> list[dict[str, Any]]:
    """Serializable profile table for the API/GUI (single source of truth)."""
    out = []
    for p in _PROFILE_LIST:
        out.append(
            {
                "id": p.id,
                "domains": list(p.domains),
                "data_type": p.data_type,
                "direction": p.direction,
                "attribute": p.attribute,
                "state_map": p.state_map,
                "transitional_states": list(p.transitional_states),
                "fault_states": list(p.fault_states),
                "action": _profile_action_dict(p.action) if p.action else None,
                "write_actions": {k: _profile_action_dict(a) for k, a in p.write_actions.items()} if p.write_actions else None,
                "unit": p.unit,
                "convention": p.convention,
                "default": p.default,
                "explicit_only": p.explicit_only,
            },
        )
    return out


def _profile_action_dict(action: ProfileAction) -> dict[str, Any]:
    return {"domain": action.domain, "name": action.name, "value_path": action.value_path}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


ValueKind = Literal["auto", "boolean", "number", "string"]
_KIND_BY_TYPE: dict[str, ValueKind] = {"BOOLEAN": "boolean", "FLOAT": "number", "INTEGER": "number", "STRING": "string"}


@dataclass(frozen=True)
class ResolvedMapping:
    mode: Literal["legacy", "write_actions", "action", "profile", "default"]
    attribute: str | None
    state_map: dict[str, Any] | None
    transitional_states: frozenset[str]
    fault_states: frozenset[str]
    value_kind: ValueKind
    action: HaAction | None = None
    write_actions: dict[str, HaAction] | None = None
    missing_values: dict[str, Any] = field(default_factory=dict)


def _materialize(action: ProfileAction, entity_id: str) -> HaAction:
    return HaAction(domain=action.domain or entity_domain(entity_id), name=action.name, value_path=action.value_path)


def resolve(bc: HaBindingConfig) -> ResolvedMapping:
    profile = PROFILES.get(bc.profile) if bc.profile else None
    attribute = bc.attribute or (profile.attribute if profile else None)
    # A profile's state_map and "missing attribute" values only apply to the value it reads.
    own_value = profile is not None and attribute == profile.attribute
    state_map = bc.state_map if bc.state_map is not None else (profile.state_map if own_value else None)
    transitional = frozenset(bc.transitional_states or (profile.transitional_states if profile else ()))
    fault = frozenset(bc.fault_states or (profile.fault_states if profile else ()))
    value_kind: ValueKind = bc.read_as or (_KIND_BY_TYPE.get(profile.data_type, "auto") if profile else "auto")

    action: HaAction | None = None
    write_actions: dict[str, HaAction] | None = None
    if uses_legacy_write(bc):
        mode = "legacy"
    elif bc.write_actions is not None:
        mode, write_actions = "write_actions", bc.write_actions
    elif bc.action is not None:
        mode, action = "action", bc.action
    elif profile is not None:
        # A read-only profile resolves to no action: it never falls back to the default.
        mode = "profile"
        if profile.action:
            action = _materialize(profile.action, bc.entity_id)
        elif profile.write_actions:
            write_actions = {k: _materialize(a, bc.entity_id) for k, a in profile.write_actions.items()}
    else:
        mode = "default"
    missing = profile.missing_values if own_value else {}
    return ResolvedMapping(mode, attribute, state_map, transitional, fault, value_kind, action, write_actions, missing)


# ---------------------------------------------------------------------------
# Inbound normalization
# ---------------------------------------------------------------------------

GOOD, UNCERTAIN, BAD = "good", "uncertain", "bad"


def coerce_state(raw: Any) -> Any:
    """Auto-convert HA state string to Python type (pre-#1392 behaviour)."""
    if raw in ("on", "true"):
        return True
    if raw in ("off", "false"):
        return False
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass
    try:
        return float(raw)
    except (ValueError, TypeError):
        pass
    return raw


def _to_number(raw: Any) -> int | float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return raw if math.isfinite(raw) else None
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass
    try:
        number = float(raw)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def normalize(mapping: ResolvedMapping, state_obj: dict[str, Any] | None) -> tuple[Any, str, bool]:
    """Map an HA state object to ``(value, quality, stable)``.

    ``stable=False`` means the value must not overwrite the DataPoint: the
    adapter republishes the last stable value with the returned quality.
    """
    if not state_obj:
        return None, BAD, False  # removed / not present
    state = state_obj.get("state")
    if state == "unavailable":
        return None, BAD, False
    if state == "unknown":
        return None, UNCERTAIN, False
    if state in mapping.fault_states:
        return None, BAD, False
    if state in mapping.transitional_states:
        return None, UNCERTAIN, False

    if mapping.attribute:
        raw = (state_obj.get("attributes") or {}).get(mapping.attribute)
        if raw is None and state in mapping.missing_values:
            return mapping.missing_values[state], GOOD, True
        if mapping.state_map is not None:
            # Map keys are text: a non-text attribute value is looked up as JSON (true, 1, null).
            return _mapped(mapping.state_map, raw if isinstance(raw, str) else json.dumps(raw))
        if mapping.value_kind in ("auto", "string") and raw is None:
            return None, GOOD, True  # e.g. no media title while idle
        return _typed(raw, mapping.value_kind, coerce=False)

    if mapping.state_map is not None:
        return _mapped(mapping.state_map, state)
    return _typed(state, mapping.value_kind, coerce=True)


def _mapped(state_map: dict[str, Any], key: Any) -> tuple[Any, str, bool]:
    if key in state_map:
        return state_map[key], GOOD, True
    return None, UNCERTAIN, False


_TRUE = (True, "on", "true")
_FALSE = (False, "off", "false")


def _typed(raw: Any, kind: ValueKind, *, coerce: bool) -> tuple[Any, str, bool]:
    """Type a raw value; a value that does not fit keeps the last one (uncertain)."""
    if kind == "auto":
        return (coerce_state(raw) if coerce else raw), GOOD, True
    if kind == "string":
        return str(raw), GOOD, True
    if kind == "number":
        value = _to_number(raw)
    elif raw in _TRUE and not _is_number(raw):
        value = True
    elif raw in _FALSE and not _is_number(raw):
        value = False
    else:
        value = None
    return (value, GOOD, True) if value is not None else (None, UNCERTAIN, False)


def _is_number(raw: Any) -> bool:
    # 1 == True / 0 == False in Python: a numeric 1 is no Boolean state
    return isinstance(raw, (int, float)) and not isinstance(raw, bool)


# ---------------------------------------------------------------------------
# Outbound action selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionCall:
    domain: str
    service: str
    body: dict[str, Any]


_BOOL_KEYS = frozenset({"true", "false"})


def action_key(logical_value: Any) -> str:
    """Key used to pick an entry of ``write_actions`` for a typed OBS value."""
    if isinstance(logical_value, bool):
        return "true" if logical_value else "false"
    if isinstance(logical_value, float) and logical_value.is_integer():
        return str(int(logical_value))
    return str(logical_value)


class NoActionForValueError(ValueError):
    """``write_actions`` has no entry for the written value."""


def _pick_write_action(write_actions: dict[str, HaAction], logical_value: Any) -> HaAction | None:
    key = action_key(logical_value)
    boolean_map = set(write_actions) <= _BOOL_KEYS
    # A Boolean profile on a numeric DataPoint (e.g. a KNX 0/1 value): 0/1 mean false/true.
    if boolean_map and key in ("0", "1") and not isinstance(logical_value, str):
        key = "true" if key == "1" else "false"
    action = write_actions.get(key)
    if action is not None:
        return action
    if boolean_map and key in _BOOL_KEYS:
        return None  # command profile: the other Boolean value intentionally does nothing
    raise NoActionForValueError(f"no action configured for value {logical_value!r} (configured: {', '.join(sorted(write_actions))})")


def _set_path(body: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = body
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _build_call(action: HaAction, entity_id: str, payload_value: Any) -> ActionCall:
    body: dict[str, Any] = dict(action.target) if action.target is not None else {"entity_id": entity_id}
    for key, value in action.data.items():
        _set_path(body, key, value)
    if action.value_path:
        _set_path(body, action.value_path, jsonable(payload_value))
    return ActionCall(action.domain, action.name, body)


def select_action(bc: HaBindingConfig, mapping: ResolvedMapping, payload_value: Any, logical_value: Any) -> ActionCall | None:
    """Resolve the HA action for a write, or None when the value maps to no action.

    The action is chosen from the typed *logical* value; only the payload uses
    the transformed value (formula/value_map).
    """
    if mapping.mode in ("legacy", "default"):
        # Unchanged pre-#1392 behaviour, keyed on the transformed value.
        domain = bc.service_domain or entity_domain(bc.entity_id)
        if bc.service_name:
            service = bc.service_name
        elif isinstance(payload_value, bool):
            service = "turn_on" if payload_value else "turn_off"
        else:
            service = "set_value"
        body: dict[str, Any] = {"entity_id": bc.entity_id}
        if bc.service_data_key and not isinstance(payload_value, bool):
            body[bc.service_data_key] = jsonable(payload_value)
        return ActionCall(domain, service, body)
    if mapping.write_actions is not None:
        action = _pick_write_action(mapping.write_actions, logical_value)
        return _build_call(action, bc.entity_id, payload_value) if action is not None else None
    if mapping.action is None:
        return None  # read-only profile
    return _build_call(mapping.action, bc.entity_id, payload_value)
