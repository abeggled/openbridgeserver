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
        # In-Bounds-Segmentwerte (#919), damit die 3-Segment-Regel — nicht der
        # Bounds-Check — den 422 auslöst.
        ("max_file_size_bytes", {"segment_max_bytes": 4 * 1024 * 1024}, {"max_file_size_bytes": 3 * 4 * 1024 * 1024 - 1}),
        ("max_entries", {"segment_max_rows": 1000}, {"max_entries": 2999}),
        ("max_age", {"segment_max_age": 300}, {"max_age": 899}),
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
async def test_config_segmented_opt_in_persists_and_exposes_store_stats(db, monkeypatch, tmp_path):
    """POST /config mit ``segmented=True`` persistiert das Flag und macht die
    Store-Stats (``store``) additiv in der Stats-API sichtbar (#919)."""
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(segmented=True, segment_max_rows=1000, max_entries=3000),
            _user="admin",
            db=db,
        )
        assert stats.enabled is True
        assert stats.store is not None
        assert "common" in stats.store and "backend_extra" in stats.store
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["segmented"] is True
        assert cfg["segment_max_rows"] == 1000
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_default_is_segmented_and_store_stats_present(db, monkeypatch, tmp_path):
    """Deployter Default (#919): ohne ``segmented`` läuft der Store segmentiert,
    ``store`` ist in den Stats sichtbar und der Default wird persistiert."""
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(_cfg(), _user="admin", db=db)
        assert stats.enabled is True
        assert stats.store is not None
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["segmented"] is True
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_explicit_opt_out_keeps_legacy_path(db, monkeypatch, tmp_path):
    """Der Legacy-Single-File-Pfad bleibt über explizites ``segmented=False``
    erreichbar (interner Test-/Legacy-Weg); ``store`` ist dann None."""
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(_cfg(segmented=False), _user="admin", db=db)
        assert stats.enabled is True
        assert stats.store is None
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["segmented"] is False
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_accepts_segments_at_valid_ratio(db, monkeypatch, tmp_path):
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(
                segment_max_bytes=4 * 1024 * 1024,
                max_file_size_bytes=3 * 4 * 1024 * 1024,
                segment_max_rows=1000,
                max_entries=3000,
            ),
            _user="admin",
            db=db,
        )
        assert stats.enabled is True
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_bytes"] == 4 * 1024 * 1024
        assert cfg["segment_max_rows"] == 1000
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "segment_kwargs"),
    [
        ("segment_max_bytes", {"segment_max_bytes": 4 * 1024 * 1024 - 1}),
        ("segment_max_bytes", {"segment_max_bytes": 1024 * 1024 * 1024 + 1}),
        ("segment_max_age", {"segment_max_age": 299}),
        ("segment_max_age", {"segment_max_age": 2_592_000 + 1}),
        ("segment_max_rows", {"segment_max_rows": 999}),
    ],
)
async def test_config_rejects_out_of_bounds_explicit_values_with_422(db, field, segment_kwargs, monkeypatch):
    """Explizite Nutzereingaben außerhalb der technischen Grenzen → HTTP 422 (#919)."""
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: ":memory:")
    try:
        with pytest.raises(HTTPException) as exc:
            await rb_api.configure_ringbuffer(_cfg(**segment_kwargs), _user="admin", db=db)
        assert exc.value.status_code == 422
        assert field in str(exc.value.detail)
    finally:
        reset_ringbuffer()
