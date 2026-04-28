# ioBroker adapter development notes

This document summarizes the ioBroker integration work so maintainers can review
the design and migrate or refine it without reconstructing the implementation
history from the diff.

## Scope

The change adds a first native `IOBROKER` adapter for open bridge server. It is
modeled after the Home Assistant adapter, but uses ioBroker Socket.IO instead of
polling or REST-style simple API calls.

Implemented capabilities:

- Connect to one or more ioBroker instances via Socket.IO.
- Subscribe to bound states and publish `stateChange` updates into OBS.
- Read an initial state value after subscription.
- Write values from OBS to ioBroker with `setState`.
- Support separate command states via `command_state_id`.
- Browse ioBroker states from the binding UI.
- Import ioBroker states into OBS datapoints and bindings.
- Write datapoint values from the object list and object detail view for live
  testing.

## Runtime architecture

The adapter lives in `obs/adapters/iobroker/adapter.py` and is registered through
the existing adapter registry. `obs/main.py` imports the module during startup,
so `IOBROKER` appears as a normal adapter type.

Instance configuration is stored in `adapter_instances.config`:

| Field | Default | Notes |
|---|---|---|
| `host` | `iobroker.local` | ioBroker host or IP address |
| `port` | `8084` | Socket.IO/Web adapter port |
| `username` | empty | Optional Basic Auth username |
| `password` | empty | Optional Basic Auth password |
| `ssl` | `false` | Uses HTTPS when enabled |
| `path` | `/socket.io` | Socket.IO path |
| `access_token` | empty | Optional bearer token |
| `resubscribe_interval_seconds` | `60` | Periodically re-subscribes and re-reads bound source states; set to `0` to disable |

Binding configuration:

| Field | Notes |
|---|---|
| `state_id` | ioBroker state used for reading/subscribing |
| `command_state_id` | Optional ioBroker state used for writes |
| `ack` | `ack` flag sent with `setState`; commands normally use `false` |
| `source_data_type` | Optional explicit incoming type: `string`, `int`, `float`, `bool`, `json` |
| `json_key` | Optional JSON key extraction for JSON values |

Binding directions follow the existing OBS semantics:

- `SOURCE`: subscribe/read from ioBroker only.
- `DEST`: write to ioBroker only.
- `BOTH`: subscribe/read and write.

## Socket.IO behavior

The implementation intentionally uses `python-socketio[asyncio_client]>=4.6,<5`
because the tested ioBroker Socket.IO adapter speaks Engine.IO v3. The adapter
keeps the Socket.IO/Engine.IO loggers at warning level to avoid noisy normal
operation logs.

On connect:

1. Publish instance status as connected.
2. Build a `state_id -> bindings` map from all `SOURCE` and `BOTH` bindings.
3. Call ioBroker `subscribe` for all mapped state IDs.
4. Read each bound state with `getState` and publish the initial value to OBS.

On `stateChange`:

1. Match the changed state ID to bound OBS bindings.
2. Extract the ioBroker value from `val`, `value`, or nested state payloads.
3. Apply automatic scalar conversion and optional OBS source transformation.
4. Publish a `DataValueEvent` with the originating binding ID.

A lightweight subscription watchdog runs by default every 60 seconds. It calls
`subscribe` again and re-reads bound source states. Unchanged values are skipped,
while drifted values are published back into OBS. This heals stale ioBroker
Socket.IO subscriptions after ioBroker adapter restarts without requiring an OBS
restart.

On write:

1. The normal OBS `WriteRouter` calls adapter `write()` for `DEST`/`BOTH`
   bindings.
2. The adapter uses `command_state_id` when configured, otherwise `state_id`.
3. The adapter calls ioBroker `setState` with `{ "val": value, "ack": ack }`.

## API additions

`obs/api/v1/adapters.py` adds ioBroker-specific helper endpoints under existing
adapter instance routes:

- `GET /api/v1/adapters/instances/{id}/iobroker/states`
  - Query parameters: `q`, `limit`.
  - Uses the live adapter instance to browse ioBroker states.
- `POST /api/v1/adapters/instances/{id}/iobroker/import-preview`
  - Returns import candidates without mutating OBS.
- `POST /api/v1/adapters/instances/{id}/iobroker/import`
  - Creates datapoints and bindings for selected import candidates.

The import only creates OBS objects for ioBroker objects of type `state`. It maps
ioBroker state types conservatively:

- `boolean` -> `BOOLEAN`
- `number` -> `FLOAT`
- `string` -> `STRING`
- unknown -> `STRING`

Each imported datapoint receives tags derived from ioBroker metadata, for
example `iobroker`, adapter namespace, role, type, and optional user-provided
tags.

## GUI additions

The GUI changes are intentionally scoped to existing views:

- `BindingForm.vue`
  - Adds an `IOBROKER` binding section.
  - Lets users search/browse state IDs from the selected ioBroker instance.
  - Keeps transformation/filter tabs hidden by default for ioBroker, but allows
    advanced options when needed.
- `AdaptersView.vue`
  - Adds an ioBroker import modal for connected ioBroker instances.
  - The modal is resizable through the shared `Modal` component.
- `DataPointsView.vue`
  - Adds a link from a row to the datapoint detail/binding view.
  - Adds inline value writing in the value column for live testing.
- `DataPointDetailView.vue`
  - Adds value writing in the current-value card when a writable binding exists.
- `gui/src/api/client.js` and `gui/src/stores/datapoints.js`
  - Add client/store helpers for datapoint writes and ioBroker browse/import
    endpoints.

GUI writes use the existing datapoint write endpoint
`POST /api/v1/datapoints/{id}/value`. This keeps ioBroker behind the normal OBS
write path: API -> EventBus -> WriteRouter -> writable adapter bindings.

## Manual validation

The feature was deployed and tested on an MM12 host with two ioBroker instances:

- ioBroker RPi: `192.168.178.20:8084`
- ioBroker CM5: `192.168.178.139:8082`

Observed behavior:

- Both adapter instances reported connected.
- State browsing returned live ioBroker states from both instances.
- Import preview and import created OBS datapoints and bindings.
- Boolean and numeric datapoints updated from ioBroker `stateChange` events.
- GUI/API writes returned `204` and were routed to ioBroker through
  `WriteRouter`.

## Automated checks

Run from the repository root:

```bash
pytest tests/adapters/test_iobroker.py
cd gui && npm install --prefer-offline && npm run build
```

Current focused result:

- `tests/adapters/test_iobroker.py`: 12 passed
- `gui` production build: passed

## Open review points

- Socket.IO version pinning is deliberately conservative for current ioBroker
  compatibility. Maintainers may want to broaden this later if newer ioBroker
  Socket.IO adapters are verified.
- Import currently maps all ioBroker `number` states to `FLOAT`; a later
  refinement could infer `INTEGER` from metadata or observed values.
- The browse endpoint uses the live adapter instance. This avoids duplicate
  connection code but means browsing is available only while the adapter is
  connected.
- The GUI live-write controls are meant as an operational testing aid. They use
  the existing authenticated datapoint write endpoint and do not bypass OBS
  authorization.
