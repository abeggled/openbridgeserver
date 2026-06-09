# KNX Device Model Traceability (#652)

This document records where the KNX device model introduced in `#508` is implemented and how it is verified.

## Scope

- Parent: `#508`
- Child issues covered here: `#647`, `#648`, `#649`, `#650`, `#651`
- RBAC tracking context: `#583` (no policy wiring in this scope)

## Traceability Matrix

| Sub-Issue | Capability | Primary code path | Verification |
|---|---|---|---|
| `#647` | KNX device schema (`V34`) | `obs/db/database.py` | `tests/unit/test_migration_knx_devices.py` |
| `#648` | KNX parser extraction for devices/comm objects/GA links | `obs/knxproj/parser.py` | `tests/unit/test_knxproj_parser_devices.py` |
| `#649` | KNX device read APIs (`/knxproj/devices*`) | `obs/api/v1/knxproj.py` | `tests/unit/test_knxproj_devices_api.py`, `tests/integration/test_knxproj_device_traceability.py` |
| `#650` | Ringbuffer device filter (`devices`/PA -> GA) | `obs/api/v1/ringbuffer.py` | `tests/unit/test_ringbuffer_device_resolution.py`, `tests/integration/test_ringbuffer_filtersets.py` |
| `#651` | GUI support for device filter in monitor/filtersets | `gui/src/views/ringbuffer/FilterEditor.vue`, `gui/src/composables/useClientSideMatch.js`, `gui/src/api/client.js` | `gui/tests/views/ringbuffer/FilterEditor.spec.js`, `gui/tests/composables/useClientSideMatch.spec.js`, `gui/tests/views/RingBufferView.filter-editor.spec.js` |

## Cross-Cutting Checks Added in #652

- `tests/integration/test_knxproj_device_traceability.py`
  - verifies KNX device graph is exposed over HTTP (`/api/v1/knxproj/devices`, `/devices/{pa}`, `/group-addresses/{ga}/devices`)
  - verifies ringbuffer device filter uses the same PA->GA mapping path and matches only expected datapoints

## Out of Scope

- RBAC policy wiring and endpoint classification from `#583` follow-up items (`#596`, `#624`, `#619`) are intentionally not implemented here.
