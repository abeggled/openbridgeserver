"""DataPoint Pydantic model — Phase 1."""

from __future__ import annotations
from typing import Any

import datetime
import uuid

from pydantic import BaseModel, Field, model_validator


class DataPoint(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str = Field(max_length=255)
    data_type: str = "UNKNOWN"
    unit: str | None = None
    tags: list[str] = Field(default_factory=list)
    mqtt_topic: str = ""
    mqtt_alias: str | None = None
    persist_value: bool = True
    record_history: bool = True
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.UTC))
    updated_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.UTC))

    @model_validator(mode="after")
    def _set_default_mqtt_topic(self) -> DataPoint:
        if not self.mqtt_topic:
            self.mqtt_topic = f"dp/{self.id}/value"
        return self


class DataPointCreate(BaseModel):
    name: str = Field(max_length=255)
    data_type: str = "UNKNOWN"
    unit: str | None = None
    tags: list[str] = Field(default_factory=list)
    mqtt_alias: str | None = None
    persist_value: bool = True
    record_history: bool = True


class DataPointUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    data_type: str | None = None
    unit: str | None = None
    tags: list[str] | None = None
    mqtt_alias: str | None = None
    persist_value: bool | None = None
    record_history: bool | None = None
    value: Any | None = None  # Allow setting the datapoint value via PATCH
