"""Visu device profiles — per-device start page and return-to-start timeout.

A Visu is typically shown on several devices at once: a phone, a wall-mounted
panel, a desktop. Each of them wants a different page to open with, and a
wall panel usually wants to fall back to its start page after nobody has
touched it for a while.

Profiles are configured centrally in the Admin GUI and stored as one JSON
document under the ``visu.devices`` app setting, so backup, export/import and
factory reset carry them along with every other app setting. A device picks
its profile once — via a link (``/visu/?device=<slug>``) or the selector on the
Visu overview — and remembers it locally.

Only *what to show* lives here. Access control stays with the Visu nodes: a
start page that needs a PIN or a login still asks for it.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

VISU_DEVICES_KEY = "visu.devices"
MAX_VISU_DEVICES = 50
MAX_RETURN_AFTER_S = 86_400  # one day
# ``/visu/?device=none`` releases a device from its profile, so no profile may be called that.
RELEASE_SLUG = "none"

# Lowercase letters, digits and inner hyphens — the slug ends up in a URL.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


class VisuDevice(BaseModel):
    slug: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    start_node_id: str | None = None
    # 0 = never return automatically.
    return_after_s: int = Field(default=0, ge=0, le=MAX_RETURN_AFTER_S)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("slug must consist of lowercase letters, digits and inner hyphens (max. 40 characters)")
        if value == RELEASE_SLUG:
            raise ValueError(f"slug '{RELEASE_SLUG}' is reserved: ?device={RELEASE_SLUG} releases a device from its profile")
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value

    @field_validator("start_node_id")
    @classmethod
    def _empty_start_is_none(cls, value: str | None) -> str | None:
        return value or None


class VisuDeviceOut(VisuDevice):
    # True when the start page no longer exists (e.g. it was deleted): the Visu then opens
    # the overview, while the Admin GUI keeps showing the stale choice until it is replaced.
    start_node_missing: bool = False


class VisuDevicesIn(BaseModel):
    devices: list[VisuDevice] = Field(default_factory=list, max_length=MAX_VISU_DEVICES)

    @model_validator(mode="after")
    def _unique_slugs(self) -> VisuDevicesIn:
        seen: set[str] = set()
        for device in self.devices:
            if device.slug in seen:
                raise ValueError(f"duplicate slug: {device.slug}")
            seen.add(device.slug)
        return self


def parse_visu_devices(raw: str) -> list[VisuDevice]:
    """Validate a stored ``visu.devices`` value; raises ``ValueError`` when invalid."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{VISU_DEVICES_KEY} is not valid JSON") from exc
    # A non-list value fails the model validation (pydantic's ValidationError is a ValueError).
    return VisuDevicesIn(devices=data).devices


def load_visu_devices(raw: str | None) -> list[VisuDevice]:
    """Read the stored profiles leniently: a broken value must not break the Visu."""
    if not raw:
        return []
    try:
        return parse_visu_devices(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s setting", VISU_DEVICES_KEY)
        return []


def dump_visu_devices(devices: list[VisuDevice]) -> str:
    return json.dumps([d.model_dump() for d in devices], ensure_ascii=False)
