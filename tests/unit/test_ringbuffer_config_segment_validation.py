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
async def test_config_rejects_too_coarse_segmentation_with_422(db, field, segment_kwargs, retention_kwargs, monkeypatch, tmp_path):
    # File-Pfad (nicht ``:memory:``): die 3-Segment-Regel gilt nur für den segmentierten
    # Pfad, und eine in-memory-DB wird ohne explizites ``segmented`` bewusst nicht
    # segmentiert (#968, Codex :2221) – die Validierung liefe dort sonst gar nicht.
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(tmp_path / "obs_ringbuffer.db"))
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
async def test_config_normalizes_zero_max_age_to_none_in_segmented_path(db, monkeypatch, tmp_path):
    """#951 [P2]: ``max_age: 0`` (erlaubt vom API-Modell) darf im segmentierten Pfad kein 422 sein.

    ``RingBufferConfig`` erlaubt ``max_age: 0`` (fuer persistierte Legacy-Configs bereits
    zu ``None`` normalisiert). Wurde die rohe 0 an ``StoreRetentionConfig`` durchgereicht,
    lehnte dessen post-init sie ab → 422, obwohl es ein gueltiger „unbegrenzt/keine
    Age-Retention"-Round-trip ist. Der Config-API-Pfad muss ``max_age == 0`` vor der
    segmentierten Validierung zu ``None`` normalisieren – konsistent zum Persisted-Load-Pfad.
    """
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(segmented=True, max_age=0),
            _user="admin",
            db=db,
        )
        assert stats.enabled is True
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["max_age"] is None
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
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
async def test_config_rejects_all_total_and_segment_limits_disabled(db, monkeypatch, tmp_path):
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        with pytest.raises(HTTPException) as exc:
            await rb_api.configure_ringbuffer(
                _cfg(
                    max_entries=None,
                    max_file_size_bytes=None,
                    max_age=None,
                    segment_max_bytes=None,
                    segment_max_rows=None,
                    segment_max_age=None,
                ),
                _user="admin",
                db=db,
            )
        assert exc.value.status_code == 422
        assert "segment rotation" in str(exc.value.detail).lower()
    finally:
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_accepts_explicit_age_rotation_with_unbounded_total_retention(db, monkeypatch, tmp_path):
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(
                max_entries=None,
                max_file_size_bytes=None,
                max_age=None,
                segment_max_bytes=None,
                segment_max_rows=None,
                segment_max_age=24 * 60 * 60,
            ),
            _user="admin",
            db=db,
        )
        assert stats.retention_unbounded is True
        assert stats.effective_segment_max_bytes is None
        assert stats.effective_segment_max_rows is None
        assert stats.effective_segment_max_age == 24 * 60 * 60
        assert stats.segment_max_bytes_source == "disabled"
        assert stats.segment_max_rows_source == "disabled"
        assert stats.segment_max_age_source == "explicit"
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_reports_per_dimension_derived_effective_limits(db, monkeypatch, tmp_path):
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(
                max_entries=30_000,
                max_file_size_bytes=3 * 1024 * 1024 * 1024,
                max_age=30 * 24 * 60 * 60,
                segment_max_bytes=None,
                segment_max_rows=None,
                segment_max_age=None,
            ),
            _user="admin",
            db=db,
        )
        assert stats.retention_unbounded is False
        assert stats.effective_segment_max_bytes == 1024 * 1024 * 1024
        assert stats.effective_segment_max_rows == 10_000
        assert stats.effective_segment_max_age == 10 * 24 * 60 * 60
        assert stats.segment_max_bytes_source == "derived"
        assert stats.segment_max_rows_source == "derived"
        assert stats.segment_max_age_source == "derived"
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()


@pytest.mark.asyncio
async def test_config_segmented_false_with_short_max_age_is_accepted(db, monkeypatch, tmp_path):
    """#951: ``segmented=false`` + kurze ``max_age`` darf NICHT mit 422 abgelehnt werden.

    Die 3-Segment-Regel läuft nur im segmentierten Modus. Ein Client, der den
    Legacy-Store behalten will (``segmented=false``, ``max_age=3600``), traf früher
    fälschlich den Default-``segment_max_age`` (21600 s) und bekam 422 —
    obwohl im Legacy-Pfad gar keine Segmente existieren. Jetzt: 200.
    """
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    try:
        stats = await rb_api.configure_ringbuffer(
            _cfg(segmented=False, max_age=3600),
            _user="admin",
            db=db,
        )
        assert stats.enabled is True
        assert stats.store is None
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert cfg["segmented"] is False
        assert cfg["max_age"] == 3600
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
