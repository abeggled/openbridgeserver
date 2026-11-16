# KNX project device snapshot transactions

The device import SQL in `_import_knx_devices_and_comm_objects()` runs inside
`Database.transaction()`. This fixes the import deadlock reported in
[issue #1394](https://github.com/abeggled/openbridgeserver/issues/1394).

## Failure mechanism

Previously, `SAVEPOINT knx_device_snapshot` opened a transaction on the shared
connection. `Database.execute()` retained `_write_lock` for its legacy write
owner until commit. An ordinary concurrent value-persistence operation could
then acquire `_memory_operation_lock` and wait for `_write_lock`.

When the importer next called `fetchall()` (for hierarchy links or existing
spaces), it tried to acquire `_memory_operation_lock` while still owning
`_write_lock`. Neither task could proceed. Stopping one adapter cannot prevent
this cycle because other persisted datapoint events can also enter the writer
path. A health endpoint that does not access the DB can remain available while
DB readiness fails.

## Transaction boundary

Schema checks and project parsing remain outside the transaction. The SQL phase
uses the existing task-local connection and operation/write lock ordering of
`Database.transaction()`. Its own reads and writes reuse that connection
instead of entering ordinary shared-connection operations.

The transaction covers replacement of devices, communication objects,
device-space links and communication-object/group-address links, preservation
of valid manual hierarchy links, and required GA placeholders. Readers through
the Database abstraction wait until the snapshot is complete rather than
observing the intermediate empty tables.

The existing SAVEPOINT is retained so a helper failure also rolls back its local
work when called inside an existing outer transaction. The trailing manual
`db.commit()` is removed: the context owns commit/rollback. The existing context
handles cancellation via `BaseException`, releases its locks and closes the
private connection. An outer explicit transaction retains its commit boundary.

The complete multi-stage project import is **not** one transaction. Earlier
GA/location/function/trade phases keep their existing commit behavior, and
device-parser errors retain their existing tolerant handling. This change does
not modify shared Database locking, DPT inference, adapter behavior, schemas,
dependencies or API responses. Ordinary DB operations may briefly wait for the
snapshot SQL phase; long project parsing does not hold that snapshot lock.

## Regression coverage

`tests/unit/test_knxproj_snapshot_transaction.py` uses disposable file databases
and the real registry persistence path with mocked MQTT. It checks:

- concurrent persistence and a reader complete, with only a complete new snapshot
  visible;
- an injected SQL-phase error restores the old devices/objects/links and releases
  waiting operations;
- `CancelledError` restores the old snapshot and releases both locks;
- a later outer-transaction error still rolls back the nested import;
- parsing runs outside the snapshot write lock;
- an empty snapshot commits successfully;
- an unknown space does not create an invalid device-space link.

`tests/integration/test_knxproj_import_concurrency.py` exercises the authenticated
HTTP endpoint with real GA/location/trade parsers, a synthetic
30-device/551-object/572-link device-parser result, concurrent real value
persistence and a device-list request. It checks the completed snapshot and
persisted value and cleans up its device fixtures. The repository demo project
does not contain a usable device snapshot, so the device-parser result is
explicitly synthetic.

Run the targeted checks with the checkout's normal development environment:

```sh
tools/with-venv python -m pytest   tests/unit/test_knxproj_import_devices.py   tests/unit/test_database_wal_snapshot_serialization.py   tests/unit/test_knxproj_snapshot_transaction.py
tools/with-venv python -m pytest   tests/integration/test_knxproj_api.py   tests/integration/test_knxproj_device_traceability.py   tests/integration/test_knxproj_import_concurrency.py
```

The integration fixtures start an isolated MQTT broker and SQLite database.
No private ETS export, production DB, KNX connection or hardware writes are
needed. Changed snapshot lines and branches are included in coverage checks.

## Operational verification and rollback

The same narrow change was verified separately on a deployed 2026.10.0 ARM64
installation: an actual ETS import completed with adapters enabled, a fresh
complete device/object/link snapshot, a successful import audit event and DB
readiness still available. That verifies the deployed patch on that version;
the PR's current-main test results are reported separately in the PR.

No migration or data conversion is required. Reverting the source change and
restarting restores the prior implementation, including its deadlock risk.
Keep the previous application artifact and a consistent DB backup before a
rollout. A source rollback does not require restoring the DB backup or discarding
subsequent imported data. If an older process is already deadlocked, changing
files alone cannot unblock it; restart that process.
