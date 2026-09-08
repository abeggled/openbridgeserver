"""Fehlgeschlagener Startup muss den Prozess beenden — nicht haengen lassen.

Uvicorn kehrt nach einem Lifespan-Fehler aus ``serve()`` zurueck, aber die
Nicht-Daemon-Threads der bereits geoeffneten aiosqlite-Verbindungen halten den
Interpreter am Leben. Ohne den Abbruch bleibt ein frisch installierter Container
dauerhaft "Up (unhealthy)", ohne Listener und ohne Restart.
"""

from __future__ import annotations

import obs.main as main_module


class _RecordingExit:
    def __init__(self) -> None:
        self.codes: list[int] = []

    def __call__(self, code: int) -> None:
        self.codes.append(code)


def _patch_cleanup(monkeypatch, *, archive_error=None, db_error=None) -> dict[str, bool]:
    calls = {"archive": False, "db": False}

    async def close_archive() -> None:
        calls["archive"] = True
        if archive_error:
            raise archive_error

    class _Db:
        async def disconnect(self) -> None:
            calls["db"] = True
            if db_error:
                raise db_error

    monkeypatch.setattr("obs.message_archive.close_message_archive_store", close_archive)
    monkeypatch.setattr("obs.db.database.get_db", lambda: _Db())
    return calls


async def test_abort_closes_resources_and_exits(monkeypatch) -> None:
    calls = _patch_cleanup(monkeypatch)
    exit_ = _RecordingExit()
    monkeypatch.setattr(main_module.os, "_exit", exit_)
    # Das echte logging.shutdown() wuerde die Handler des Test-Runners schliessen.
    monkeypatch.setattr(main_module.logging, "shutdown", lambda: None)

    await main_module._abort_failed_startup()

    assert calls == {"archive": True, "db": True}
    assert exit_.codes == [main_module._STARTUP_FAILED_EXIT_CODE]
    assert main_module._STARTUP_FAILED_EXIT_CODE != 0


async def test_abort_passes_uvicorns_exit_code_through(monkeypatch) -> None:
    _patch_cleanup(monkeypatch)
    exit_ = _RecordingExit()
    monkeypatch.setattr(main_module.os, "_exit", exit_)
    monkeypatch.setattr(main_module.logging, "shutdown", lambda: None)

    await main_module._abort_failed_startup(4)

    assert exit_.codes == [4]


async def test_abort_exits_even_when_cleanup_fails(monkeypatch) -> None:
    calls = _patch_cleanup(
        monkeypatch,
        archive_error=RuntimeError("archive never opened"),
        db_error=RuntimeError("db never opened"),
    )
    exit_ = _RecordingExit()
    monkeypatch.setattr(main_module.os, "_exit", exit_)
    # Das echte logging.shutdown() wuerde die Handler des Test-Runners schliessen.
    monkeypatch.setattr(main_module.logging, "shutdown", lambda: None)

    await main_module._abort_failed_startup()

    assert calls == {"archive": True, "db": True}
    assert exit_.codes == [main_module._STARTUP_FAILED_EXIT_CODE]


class _ServerStub:
    def __init__(self, started: bool, error: BaseException | None) -> None:
        self.started = started
        self.error = error

    async def serve(self) -> None:
        if self.error is not None:
            raise self.error


def _patch_server(monkeypatch, *, started: bool, error: BaseException | None = None) -> list[int]:
    aborted: list[int] = []

    async def fake_abort(exit_code: int = main_module._STARTUP_FAILED_EXIT_CODE) -> None:
        aborted.append(exit_code)

    monkeypatch.setattr(main_module, "create_app", lambda: object())
    monkeypatch.setattr(main_module.uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main_module.uvicorn, "Server", lambda _config: _ServerStub(started, error))
    monkeypatch.setattr(main_module, "_abort_failed_startup", fake_abort)
    return aborted


async def test_main_aborts_on_uvicorns_startup_failure_exit(monkeypatch) -> None:
    # uvicorn.server.Server.startup() ruft sys.exit(3) *innerhalb* von serve() —
    # ohne das Abfangen liefe der Prozess in den haengenden Interpreter-Exit.
    aborted = _patch_server(monkeypatch, started=False, error=SystemExit(3))

    await main_module.main()

    assert aborted == [3]


async def test_main_falls_back_to_the_default_code_for_a_non_integer_exit(monkeypatch) -> None:
    aborted = _patch_server(monkeypatch, started=False, error=SystemExit(None))

    await main_module.main()

    assert aborted == [main_module._STARTUP_FAILED_EXIT_CODE]


async def test_main_aborts_when_startup_never_completed(monkeypatch) -> None:
    # Kein SystemExit, aber auch kein Listener — z. B. Signal waehrend des Startups.
    aborted = _patch_server(monkeypatch, started=False)

    await main_module.main()

    assert aborted == [main_module._STARTUP_FAILED_EXIT_CODE]


async def test_main_returns_normally_after_a_clean_shutdown(monkeypatch) -> None:
    aborted = _patch_server(monkeypatch, started=True)

    await main_module.main()

    assert aborted == []
