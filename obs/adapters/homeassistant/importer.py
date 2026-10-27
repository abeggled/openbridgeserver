"""Home Assistant entity browser filters and import planning (#1392).

Pure functions: the API layer fetches entities from the live adapter and the
existing bindings from the database, this module decides what an import would
create.  Nothing here mutates anything, so the preview and the import share
exactly the same plan.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from obs.adapters.homeassistant.profiles import (
    AMBIGUOUS_DOMAINS,
    ATTRIBUTE_PATTERN,
    ENTITY_ID_PATTERN,
    PROFILES,
    HaBindingConfig,
    HaProfile,
    default_profile,
    profile_supported,
    profiles_for_domain,
    resolve,
)

NAME_MAX = 255  # DataPointCreate.name


class HaImportSelection(BaseModel):
    entity_id: str = Field(pattern=ENTITY_ID_PATTERN, max_length=255)
    attribute: str | None = Field(default=None, pattern=ATTRIBUTE_PATTERN, max_length=255)
    # "auto" = proposed profile (visible in the preview); None = plain value, no profile
    profile: str | None = Field(default="auto", max_length=64)
    direction: Literal["auto", "SOURCE", "DEST", "BOTH"] = "auto"
    # Name of the DataPoint to create; None/blank = the proposed name
    name: str | None = Field(default=None, max_length=NAME_MAX)

    @field_validator("name")
    @classmethod
    def _blank_name_is_proposal(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class HaImportRequest(BaseModel):
    selections: list[HaImportSelection] = Field(default_factory=list, max_length=500)
    tags: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=20)
    persist_value: bool = True
    record_history: bool = True


class HaImportCandidate(BaseModel):
    entity_id: str
    attribute: str | None = None
    profile: str | None = None
    name: str
    proposed_name: str = ""  # what the name is without the user's override
    selection: int = 0  # index of the selection this candidate comes from
    data_type: str
    unit: str | None = None
    direction: str
    tags: list[str]
    config: dict[str, Any]
    mapping: dict[str, Any] | None = None
    convention: str | None = None
    status: Literal["new", "exists", "conflict", "unsupported"] = "new"
    reason: str | None = None
    existing_binding_id: str | None = None


# ---------------------------------------------------------------------------
# Browse filters
# ---------------------------------------------------------------------------


def filter_entities(
    entities: list[dict[str, Any]],
    *,
    q: str = "",
    domain: str = "",
    area: str = "",
    device: str = "",
    platform: str = "",
    availability: str = "",
    entity_id: str = "",
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Filter, facet and page the entity list; *entity_id* matches exactly."""
    needle = q.strip().lower()
    exact = entity_id.strip().lower()

    def matches(item: dict[str, Any], skip: str = "") -> bool:
        if exact and item["entity_id"] != exact:
            return False
        if needle and needle not in item["entity_id"].lower() and needle not in str(item.get("name") or "").lower():
            return False
        if skip != "domain" and domain and item["domain"] != domain:
            return False
        if skip != "area" and area and item.get("area") != area:
            return False
        if skip != "device" and device and item.get("device") != device:
            return False
        if skip != "platform" and platform and item.get("platform") != platform:
            return False
        if availability == "available" and not item.get("available"):
            return False
        return not (availability == "unavailable" and item.get("available"))

    hits = [item for item in entities if matches(item)]

    def facet(key: str) -> list[str]:
        return sorted({str(item[key]) for item in entities if item.get(key) and matches(item, skip=key)})

    return {
        "total": len(hits),
        "items": [_with_profiles(item) for item in hits[offset : offset + limit]],
        "facets": {"domain": facet("domain"), "area": facet("area"), "device": facet("device"), "platform": facet("platform")},
    }


def _state_obj(item: dict[str, Any]) -> dict[str, Any]:
    # capabilities: non-scalar attributes the profile checks need (never offered for import)
    return {"state": item.get("state"), "attributes": {**(item.get("attributes") or {}), **(item.get("capabilities") or {})}}


def _with_profiles(item: dict[str, Any]) -> dict[str, Any]:
    state_obj = _state_obj(item)
    proposed = default_profile(item["domain"], state_obj)
    profiles = []
    for profile in profiles_for_domain(item["domain"]):
        ok, reason = profile_supported(profile, state_obj)
        profiles.append({"id": profile.id, "supported": ok, "reason": reason})
    return {**item, "profiles": profiles, "default_profile": proposed.id if proposed else None}


# ---------------------------------------------------------------------------
# Import planning
# ---------------------------------------------------------------------------


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        # A whole number now may be fractional later (21 -> 21.5): numbers read as FLOAT.
        return "FLOAT"
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return "STRING"
        # "nan"/"inf" parse as floats, but a number binding never accepts them.
        return "FLOAT" if math.isfinite(number) else "STRING"
    return "STRING"


_READ_AS = {"BOOLEAN": "boolean", "INTEGER": "number", "FLOAT": "number", "STRING": "string"}


def _state_type(item: dict[str, Any]) -> str:
    """Conservative OBS type for a plain state read; the binding reads it as exactly that type."""
    state = item.get("state")
    if state in ("on", "off") and item["domain"] not in AMBIGUOUS_DOMAINS:
        return "BOOLEAN"
    if state in ("unknown", "unavailable", None):
        return "FLOAT" if item.get("unit") else "STRING"
    return "FLOAT" if _infer_type(state) == "FLOAT" else "STRING"


def mapping_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Human-readable resolved mapping, as shown by preview and the binding form."""
    bc = HaBindingConfig(**config)
    mapping = resolve(bc)
    write: Any = None
    write_kind = "none"  # "single" action or value-keyed "map"; a value key may be called "name"
    if mapping.mode in ("legacy", "default"):
        write = {"mode": mapping.mode}
    elif mapping.write_actions is not None:
        write = {k: a.model_dump(exclude_defaults=True) for k, a in mapping.write_actions.items()}
        write_kind = "map"
    elif mapping.action is not None:
        write = mapping.action.model_dump(exclude_defaults=True)
        write_kind = "single"
    return {
        "mode": mapping.mode,
        "write_kind": write_kind,
        "read": {
            "attribute": mapping.attribute,
            "state_map": mapping.state_map,
            "transitional_states": sorted(mapping.transitional_states),
            "fault_states": sorted(mapping.fault_states),
        },
        "write": write,
    }


def _choose_profile(sel: HaImportSelection, item: dict[str, Any]) -> tuple[HaProfile | None, str | None]:
    domain = item["domain"]
    state_obj = _state_obj(item)
    if sel.profile is None:
        return None, None
    if sel.profile == "auto":
        if sel.attribute:
            for profile in profiles_for_domain(domain):
                if profile.attribute == sel.attribute and not profile.explicit_only and profile_supported(profile, state_obj)[0]:
                    return profile, None
            return None, None
        return default_profile(domain, state_obj), None
    profile = PROFILES.get(sel.profile)
    if profile is None or domain not in profile.domains or (sel.attribute and sel.attribute != profile.attribute):
        return None, "profileNotApplicable"
    ok, reason = profile_supported(profile, state_obj)
    return (profile, None) if ok else (None, reason)


def _name(item: dict[str, Any], sel: HaImportSelection, profile: HaProfile | None) -> str:
    base = str(item.get("name") or item["entity_id"])
    if sel.attribute:
        base = f"{base} ({sel.attribute})"
    elif profile is not None and profile is not default_profile(item["domain"], _state_obj(item)):
        # Only the entity's proposed profile keeps the plain name: two default profiles
        # of one entity (cover open/closed + position) must not share it.
        base = f"{base} ({profile.id.split('.', 1)[1]})"
    return base[:NAME_MAX]  # a long friendly name must not make the import fail


def _tags(item: dict[str, Any], extra: list[str]) -> list[str]:
    tags = ["homeassistant", item["domain"]]
    for key in ("platform", "area", "device_class"):
        if item.get(key):
            tags.append(str(item[key]))
    tags.extend(t.strip() for t in extra if t.strip())
    seen: set[str] = set()
    return [t for t in tags if not (t in seen or seen.add(t))]


def _identity(config: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return str(config.get("entity_id")), config.get("attribute") or None, config.get("profile") or None


def _read_source(config: dict[str, Any]) -> tuple[str, str | None] | None:
    """(entity_id, attribute) a mapping reads; None for a pure command profile."""
    profile = PROFILES.get(config.get("profile") or "")
    if profile is not None and profile.direction == "DEST":
        return None
    attribute = config.get("attribute") or (profile.attribute if profile else None)
    return str(config.get("entity_id")), attribute or None


def plan_import(
    entities: list[dict[str, Any]],
    request: HaImportRequest,
    existing: list[tuple[str, dict[str, Any], str]],
) -> list[HaImportCandidate]:
    """Plan the import. *existing* holds (binding_id, config, direction) of this instance."""
    # Stored configs keep what the user typed; the adapter matches entity ids lowercased.
    existing = [(binding_id, {**cfg, "entity_id": str(cfg.get("entity_id")).strip().lower()}, direction) for binding_id, cfg, direction in existing]
    by_id = {item["entity_id"]: item for item in entities}
    by_identity = {_identity(cfg): binding_id for binding_id, cfg, _direction in existing}
    # Same entity value, mapped by hand (no profile) on one side: importing it again
    # would create a second object for the same device value. Only reading bindings count.
    manual_reads: dict[tuple[str, str | None], str] = {}
    profiled_reads: dict[tuple[str, str | None], str] = {}
    for binding_id, cfg, direction in existing:
        source = _read_source(cfg)
        if source is not None and direction in ("SOURCE", "BOTH"):
            (profiled_reads if cfg.get("profile") else manual_reads).setdefault(source, binding_id)
    # The same check between candidates of this request.
    planned_manual: dict[tuple[str, str | None], str] = {}
    planned_profiled: dict[tuple[str, str | None], str] = {}
    by_uid = {
        (cfg.get("ha_uid"), cfg.get("attribute") or None, cfg.get("profile") or None): (binding_id, cfg)
        for binding_id, cfg, _direction in existing
        if cfg.get("ha_uid")
    }

    result: list[HaImportCandidate] = []
    planned: dict[tuple[str, str | None, str | None], int] = {}
    for index, sel in enumerate(request.selections):
        item = by_id.get(sel.entity_id)
        if item is None:
            result.append(_unsupported(sel, index, "entityNotFound"))
            continue
        profile, reason = _choose_profile(sel, item)
        if reason:
            result.append(_unsupported(sel, index, reason))
            continue
        candidate = _candidate(item, sel, profile, request.tags)
        candidate.selection = index
        if candidate.status == "unsupported":
            result.append(candidate)
            continue

        identity = _identity(candidate.config)
        if identity in planned:
            # The same binding selected twice (e.g. an attribute and its profile):
            # plan it once, with the wider direction the user asked for.
            first = result[planned[identity]]
            if sel.name and request.selections[first.selection].name is None:
                first.name = sel.name  # the name edited for the merged selection
            if candidate.direction != first.direction:
                first.direction = "BOTH"  # SOURCE + DEST, or either + BOTH
                # It may read only now: check and register that read like a new one.
                _check_read_overlap(first, manual_reads, profiled_reads, planned_manual, planned_profiled)
                _register_planned_read(first, planned_manual, planned_profiled)
            continue
        planned[identity] = len(result)
        if identity in by_identity:
            candidate.status, candidate.reason = "exists", "bindingExists"
            candidate.existing_binding_id = by_identity[identity]
        elif (uid_match := by_uid.get((candidate.config.get("ha_uid"), identity[1], identity[2]))) is not None:
            # Same HA registry entry under another entity_id: renamed in HA.
            candidate.status, candidate.reason = "conflict", "entityRenamed"
            candidate.existing_binding_id = uid_match[0]
        else:
            _check_read_overlap(candidate, manual_reads, profiled_reads, planned_manual, planned_profiled)
        _register_planned_read(candidate, planned_manual, planned_profiled)
        result.append(candidate)
    return result


def _check_read_overlap(candidate: HaImportCandidate, manual_reads: dict, profiled_reads: dict, planned_manual: dict, planned_profiled: dict) -> None:
    """Mark a new candidate that would read a value someone already reads as a conflict."""
    if candidate.status != "new" or candidate.direction == "DEST":
        return  # a write-only candidate adds no second reader
    if (overlap := _overlap(candidate.config, manual_reads, profiled_reads)) is not None:
        candidate.status, candidate.reason = "conflict", "entityAlreadyBound"
        candidate.existing_binding_id = overlap
    elif _overlap(candidate.config, planned_manual, planned_profiled) is not None:
        candidate.status, candidate.reason = "conflict", "selectedTwice"


def _register_planned_read(candidate: HaImportCandidate, planned_manual: dict, planned_profiled: dict) -> None:
    """Remember what a planned new candidate reads, for the duplicate check of later ones."""
    source = _read_source(candidate.config)
    if candidate.status == "new" and source is not None and candidate.direction != "DEST":
        (planned_profiled if candidate.profile else planned_manual).setdefault(source, candidate.entity_id)


def _overlap(config: dict[str, Any], manual_reads: dict, profiled_reads: dict) -> str | None:
    source = _read_source(config)
    if source is None:
        return None
    # A manual existing binding conflicts with any candidate; a manual candidate with any binding.
    return manual_reads.get(source) or (profiled_reads.get(source) if not config.get("profile") else None)


def _unsupported(sel: HaImportSelection, index: int, reason: str) -> HaImportCandidate:
    return HaImportCandidate(
        entity_id=sel.entity_id,
        attribute=sel.attribute,
        profile=sel.profile if sel.profile != "auto" else None,
        name=sel.entity_id,
        proposed_name=sel.entity_id,
        selection=index,
        data_type="STRING",
        direction=sel.direction,
        tags=[],
        config={},
        status="unsupported",
        reason=reason,
    )


def _candidate(item: dict[str, Any], sel: HaImportSelection, profile: HaProfile | None, extra_tags: list[str]) -> HaImportCandidate:
    attrs = item.get("attributes") or {}
    domain = item["domain"]
    reason: str | None = None

    if profile is not None:
        data_type = _state_type(item) if profile.data_type == "AUTO" else profile.data_type
        # Ticking an attribute means "read this value": it never silently becomes a write binding.
        default_direction = "SOURCE" if sel.attribute and profile.direction == "BOTH" else profile.direction
        unit = profile.unit or (item.get("unit") if profile.attribute is None else None)
        read_attribute = None  # the profile defines what is read
    elif sel.attribute:
        if sel.attribute not in attrs:
            reason = "attributeUnavailable"
        data_type = _infer_type(attrs.get(sel.attribute))
        default_direction, unit, read_attribute = "SOURCE", None, sel.attribute
    else:
        data_type, default_direction, unit, read_attribute = _state_type(item), "SOURCE", item.get("unit"), None

    direction = default_direction if sel.direction == "auto" else sel.direction
    if reason is None and direction in ("DEST", "BOTH"):
        if profile is None:
            reason = "writeNeedsProfile"
        elif profile.direction == "SOURCE":
            reason = "profileReadOnly"
    if reason is None and direction in ("SOURCE", "BOTH") and profile is not None and profile.direction == "DEST":
        reason = "profileWriteOnly"

    config: dict[str, Any] = {"entity_id": item["entity_id"]}
    if read_attribute:
        config["attribute"] = read_attribute
    if profile is not None:
        config["profile"] = profile.id
    if profile is None or profile.data_type == "AUTO":
        # Read as the type the DataPoint gets, so later values never mismatch it.
        config["read_as"] = _READ_AS[data_type]
    if item.get("ha_uid"):
        config["ha_uid"] = item["ha_uid"]

    candidate = HaImportCandidate(
        entity_id=item["entity_id"],
        attribute=sel.attribute,
        profile=profile.id if profile else None,
        name=sel.name or _name(item, sel, profile),
        proposed_name=_name(item, sel, profile),
        data_type=data_type,
        unit=unit,
        direction=direction,
        tags=_tags(item, extra_tags),
        config=config,
        convention=profile.convention if profile else None,
    )
    if reason is None and profile is None and domain in AMBIGUOUS_DOMAINS and sel.attribute is None:
        candidate.reason = "ambiguousReadOnly"  # informational: imported read-only
    if reason is not None:
        candidate.status, candidate.reason = "unsupported", reason
        return candidate
    candidate.mapping = mapping_summary(config)
    return candidate
