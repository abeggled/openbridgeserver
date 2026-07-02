"""Writer-Exklusivität pro Storage-Root via Lockfile/Lease (#931).

Backend-intern (unter der portablen Store-Grenze). Ergänzt das prozess-interne
asyncio-Lock des bestehenden ``RingBuffer`` um eine **root-weite** Absicherung:
genau ein Writer darf eine Storage-Root besitzen.

Modell: ein ``writer.lock``-File in der Root hält PID + Zeitstempel. Ein zweiter
Writer auf derselben Root wird **fail-fast** mit ``WriterLockHeldError``
abgewiesen. Ein verwaistes Lockfile eines nicht mehr laufenden Prozesses (PID
existiert nicht mehr) darf übernommen werden, damit ein Absturz die Root nicht
dauerhaft blockiert.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

LOCK_FILENAME = "writer.lock"


class WriterLockHeldError(RuntimeError):
    """Raised when another live writer already owns the storage root."""


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Prozess existiert, gehört aber einem anderen User → als lebendig werten.
        return True
    return True


class WriterLease:
    """Root-weite Writer-Lease über ein Lockfile."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._lock_path = self._root / LOCK_FILENAME
        self._owns = False

    @property
    def owns_lock(self) -> bool:
        return self._owns

    async def acquire(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        # Atomarer Erwerb (#951): ``O_CREAT | O_EXCL`` legt das Lockfile in EINEM
        # nicht-teilbaren Syscall an und schlägt fehl, wenn es schon existiert. So
        # können zwei quasi-gleichzeitig startende Writer NICHT beide den alten
        # ``exists()``-Check passieren und beide ``_owns=True`` setzen — genau ein
        # Prozess gewinnt das Rennen. Existiert das File bereits, wird — wie
        # bisher — entschieden, ob es ein verwaistes (übernehmbares) oder ein
        # lebendes (fail-fast) Lock ist.
        try:
            self._create_lock_exclusive()
        except FileExistsError:
            self._take_over_or_fail()
        self._owns = True

    def _create_lock_exclusive(self) -> None:
        fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, self._lock_payload_bytes())
        finally:
            os.close(fd)

    def _take_over_or_fail(self) -> None:
        holder_pid = self._read_holder_pid()
        if holder_pid is not None and _pid_is_alive(holder_pid):
            raise WriterLockHeldError(f"storage root {self._root} is locked by live writer pid={holder_pid}")
        # Verwaistes Lockfile eines toten/unbekannten Prozesses → atomar übernehmen:
        # altes File entfernen, dann exklusiv neu anlegen. Verliert man dabei das
        # Rennen gegen einen anderen Übernehmer (erneut FileExistsError), gilt der
        # andere als Halter → fail-fast.
        try:
            self._lock_path.unlink(missing_ok=True)
            self._create_lock_exclusive()
        except FileExistsError as exc:
            raise WriterLockHeldError(f"storage root {self._root} was locked by a concurrent writer") from exc

    def _read_holder_pid(self) -> int | None:
        try:
            payload = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        pid = payload.get("pid")
        return int(pid) if isinstance(pid, int) else None

    def _lock_payload_bytes(self) -> bytes:
        payload = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        return json.dumps(payload).encode("utf-8")

    async def release(self) -> None:
        if not self._owns:
            return
        try:
            self._lock_path.unlink(missing_ok=True)
        finally:
            self._owns = False
