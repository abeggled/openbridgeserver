"""
Visu-Modelle — Pydantic-Schemas für das Visualisierungs-System

VisuNode: Knoten im Gebäudebaum (LOCATION oder PAGE)
PageConfig: Seiten-Konfiguration mit Widget-Liste
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Typen ─────────────────────────────────────────────────────────────────────

NodeType   = Literal["LOCATION", "PAGE"]
AccessLevel = Literal["readonly", "public", "protected", "private"]


# ── WidgetInstance ────────────────────────────────────────────────────────────

class WidgetInstance(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""                           # frei wählbarer Widget-Name
    type: str
    datapoint_id: str | None = None
    status_datapoint_id: str | None = None   # optionaler Rückmelde-DP
    x: int = 0
    y: int = 0
    w: int = 2
    h: int = 2
    config: dict[str, Any] = Field(default_factory=dict)


# ── PageConfig ────────────────────────────────────────────────────────────────

class PageConfig(BaseModel):
    grid_cols: int = 12
    grid_row_height: int = 80
    grid_cell_width: int = 80   # feste Zellbreite in Pixeln (WYSIWYG)
    background: str | None = None
    widgets: list[WidgetInstance] = Field(default_factory=list)


# ── VisuNode ──────────────────────────────────────────────────────────────────

class VisuNode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: str | None = None
    name: str
    type: NodeType = "PAGE"
    order: int = 0
    icon: str | None = None
    access: AccessLevel | None = None   # None = von Elternknoten erben
    access_pin: str | None = None       # bcrypt-Hash, nie im Klartext
    page_config: PageConfig | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Request-Schemas ───────────────────────────────────────────────────────────

class VisuNodeCreate(BaseModel):
    parent_id: str | None = None
    name: str
    type: NodeType = "PAGE"
    order: int = 0
    icon: str | None = None
    access: AccessLevel | None = None
    access_pin: str | None = None       # Klartext — wird im Endpoint gehasht


class VisuNodeUpdate(BaseModel):
    name: str | None = None
    order: int | None = None
    icon: str | None = None
    access: AccessLevel | None = None
    access_pin: str | None = None       # Klartext — wird im Endpoint gehasht


class PinAuthRequest(BaseModel):
    pin: str


class PinAuthResponse(BaseModel):
    session_token: str
    expires_in: int = 3600


class CopyNodeRequest(BaseModel):
    target_parent_id: str
    new_name: str


class MoveNodeRequest(BaseModel):
    new_parent_id: str | None = None
    order: int = 0
