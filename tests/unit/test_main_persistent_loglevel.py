from __future__ import annotations

import pytest

from obs.main import _read_persistent_log_level


class FakeDb:
    def __init__(self, row=None, exc: Exception | None = None) -> None:
        self.row = row
        self.exc = exc

    async def fetchone(self, _sql: str):
        if self.exc:
            raise self.exc
        return self.row


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"value": "debug"}, "DEBUG"),
        ({"value": "INFO"}, "INFO"),
        ({"value": "trace"}, None),
        ({"value": ""}, None),
        (None, None),
    ],
)
async def test_read_persistent_log_level(row, expected):
    assert await _read_persistent_log_level(FakeDb(row=row)) == expected


async def test_read_persistent_log_level_ignores_db_errors():
    assert await _read_persistent_log_level(FakeDb(exc=RuntimeError("missing table"))) is None
