"""DataPoint Pydantic model — Phase 1."""
from __future__ import annotations

import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DataPoint(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str = Field(max_length=255)
    data_type: str = "UNKNOWN"
    unit: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    mqtt_topic: str = ""
    mqtt_alias: Optional[str] = None
    persist_value: bool = True
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    @model_validator(mode="after")
    def _set_default_mqtt_topic(self) -> "DataPoint":
        if not self.mqtt_topic:
            self.mqtt_topic = f"dp/{self.id}/value"
        return self


class DataPointCreate(BaseModel):
    name: str = Field(max_length=255)
    data_type: str = "UNKNOWN"
    unit: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    mqtt_alias: Optional[str] = None
    persist_value: bool = True


class DataPointUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    data_type: Optional[str] = None
    unit: Optional[str] = None
    tags: Optional[list[str]] = None
    mqtt_alias: Optional[str] = None
    persist_value: Optional[bool] = None
