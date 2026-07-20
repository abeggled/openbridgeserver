"""Zweischichtiger RingBuffer-Store (#919/#930/#931).

Schicht 1 — **portabler ``RingBufferStore``-Contract** (``interface``):
engine-neutral (``append``/``query``/``stats``/``enforce_retention``) plus
``StoreCapabilities``-Deskriptor. Das ist, was OBS und ein späterer
``ringbufferd`` sehen; alternative Engines (PostgresTS, Influx) könnten
denselben Contract erfüllen.

Schicht 2 — **SQLite-Backend-Interna** (``manifest``, ``writer_lock``,
``sqlite_backend``, ``config``): Segmente, Manifest, ``segment_id``, Rotation,
Writer-Lock/Lease, WAL/Checkpoint. Diese Konzepte liegen **unter** der
portablen Grenze und sind nicht Teil des Contracts.
"""
