"""Writer-Exklusivität pro Storage-Root via Lockfile/Lease (#931).

Backend-intern (unter der portablen Store-Grenze). Ergänzt das prozess-interne
asyncio-Lock des bestehenden ``RingBuffer`` um eine **root-weite** Absicherung:
genau ein Writer darf eine Storage-Root besitzen.

Modell: ein ``writer.lock``-File in der Root hält PID + Zeitstempel. Ein zweiter
Writer auf derselben Root wird **fail-fast** mit ``WriterLockHeldError``
abgewiesen. Ein verwaistes Lockfile eines nicht mehr laufenden Prozesses (PID
existiert nicht mehr) darf übernommen werden, damit ein Absturz die Root nicht
dauerhaft blockiert.

Rennsicherheit (#951): Der Besitz wird über einen **flock (``LOCK_EX | LOCK_NB``)
auf dem geöffneten Lockfile-fd** entschieden und für die gesamte Lease-Lebensdauer
gehalten. Der flock ist kernel-serialisiert – zwei quasi-gleichzeitige Writer
können ihn NIE beide halten. Damit ist auch der Stale-Takeover atomar: statt das
alte File blind zu ``unlink()``en (was das frisch erzeugte Lock eines anderen
Übernehmers löschen könnte), gewinnt genau der Prozess, der den flock exklusiv
erhält, überschreibt die Payload **in place** (ohne unlink) und hält den fd.
Jeder weitere Übernehmer scheitert am ``LOCK_NB`` → fail-fast.

Autoritativer flock (#951): Sobald der exklusive ``flock`` gewonnen ist, ist er
AUTORITATIV. Die im Lockfile hinterlegte PID ist rein informativ und wird NICHT
mehr gegen Lebendigkeit geprüft. Nach einem unsauberen Shutdown kann das File auf
der Platte liegen bleiben, während der Kernel-``flock`` weg ist; in Containern
bekommt der Dienst dann oft dieselbe PID wieder (häufig PID 1). Eine PID-basierte
Ablehnung würde den Store nach jedem Absturz blockieren – der gewonnene ``flock``
allein garantiert bereits, dass genau ein Halter existiert.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path

LOCK_FILENAME = "writer.lock"


class WriterLockHeldError(RuntimeError):
    """Raised when another live writer already owns the storage root."""


class WriterLease:
    """Root-weite Writer-Lease über ein Lockfile mit gehaltenem flock."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._lock_path = self._root / LOCK_FILENAME
        self._owns = False
        self._fd: int | None = None

    @property
    def owns_lock(self) -> bool:
        return self._owns

    async def acquire(self) -> None:
        self._acquire_sync()

    def _acquire_sync(self) -> None:
        """Synchroner Kern des Erwerbs – rennsicher über einen gehaltenen flock.

        1. Lockfile ``O_CREAT``-öffnen (legt es an, falls es fehlt; teilt es sonst).
        2. ``flock(LOCK_EX | LOCK_NB)`` – kernel-serialisiert. Wer ihn erhält, ist
           der einzige Kandidat und Halter; wer ihn nicht bekommt, wird fail-fast
           abgewiesen (ein lebender Halter hält den fd offen).
        3. Unter gehaltenem flock ist der Besitz AUTORITATIV (#951): die (evtl.
           verwaiste) Payload wird ohne PID-Liveness-Prüfung **in place** mit der
           eigenen Identität überschrieben. Kein unlink → kein Fenster, in dem ein
           zweiter Übernehmer das frische Lock löscht.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                # flock von einem lebenden Halter gehalten → fail-fast. Der Halter
                # ist definitionsgemäß am Leben (er hält den fd offen).
                holder_pid = self._read_holder_pid(fd)
                raise WriterLockHeldError(f"storage root {self._root} is locked by live writer pid={holder_pid}") from exc
            # flock erhalten – ab hier AUTORITATIV (#951 [P1]): der kernel-serialisierte
            # flock stellt sicher, dass nur DIESER Prozess die Root hält; ein zweiter
            # lebender Halter wäre oben am ``LOCK_NB`` gescheitert. Die im Lockfile
            # stehende PID ist rein informativ und kann nach einem unsauberen Shutdown
            # verwaist auf der Platte liegen bleiben (in Containern bekommt der Dienst
            # oft dieselbe PID wieder, häufig PID 1). Sie darf daher NICHT mehr zur
            # Ablehnung führen – sonst startet der Store nach jedem Absturz nicht mehr.
            # Wir übernehmen die (evtl. verwaiste) Payload und überschreiben sie in place
            # mit unserer eigenen Identität.
            self._write_payload(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        self._owns = True

    def _read_holder_pid(self, fd: int) -> int | None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 4096)
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return None
        pid = payload.get("pid")
        return int(pid) if isinstance(pid, int) else None

    def _write_payload(self, fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, self._lock_payload_bytes())
        os.fsync(fd)

    def _lock_payload_bytes(self) -> bytes:
        payload = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        return json.dumps(payload).encode("utf-8")

    async def release(self) -> None:
        if not self._owns:
            return
        fd = self._fd
        try:
            # Das Lockfile beim Release NICHT mehr unlinken (#951 [P2]): der gehaltene
            # ``flock`` ist die autoritative Sperre, nicht die Datei-Existenz. Ein
            # ``unlink()`` löschte die Datei, WÄHREND ein anderer Prozess sie zwischen
            # unserem unlink und close bereits neu ``O_CREAT``-öffnen und einen SEPARATEN
            # flock auf dem neuen Inode erwerben könnte → zwei Writer auf derselben Root.
            # Wir geben nur den gehaltenen fd frei (``LOCK_UN``/``close``); die verwaiste,
            # inhaltlich harmlose Datei bleibt liegen und wird beim nächsten Start per
            # Takeover (flock-Erwerb) übernommen.
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            self._fd = None
            self._owns = False
