"""API-Config-Validierung für Segment vs. Retention (#930).

POST /config lehnt zu grobe Segmentierung im Verhältnis zu aktiven
Retention-Limits mit HTTP 422 ab (nicht auto-korrigieren). Segment-Parameter
werden persistiert und im Stats-Contract sichtbar.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import obs.api.v1.ringbuffer as rb_api
from obs.db.database import Database
from obs.ringbuffer.ringbuffer import reset_ringbuffer


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _cfg(**kwargs):
    return rb_api.RingBufferConfig(enabled=True, storage="file", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "segment_kwargs", "retention_kwargs"),
    [
        ("max_file_size_bytes", {"segment_max_bytes": 1000}, {"max_file_size_bytes": 2999}),
        ("max_entries", {"segment_max_rows": 100}, {"max_entries": 299}),
        ("max_age", {"segment_max_age": 60}, {"max_age": 179}),
    ],
)
async def test_config_rejects_too_coarse_segmentation_with_422(db, field, segment_kwargs, retention_kwargs, monkeypatch):
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: ":memory:")
    try:
        with pytest.raises(HTTPException) as exc:
            await rb_api.configure_ringbuffer(
                _cfg(**segment_kwargs, **retention_kwargs),
                _user="admin",
                db=db,
            )
        assert exc.value.status_code == 422
        assert field in str(exc.value.detail)
    finally:
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_accepts_segments_at_valid_ratio(db, monkeypatch, tmp_path):
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(
                segment_max_bytes=1000,
                max_file_size_bytes=3000,
                segment_max_rows=100,
                max_entries=300,
            ),
            _user="admin",
            db=db,
        )
        assert stats.enabled is True
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_bytes"] == 1000
        assert cfg["segment_max_rows"] == 100
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()
