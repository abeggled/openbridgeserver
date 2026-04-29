# HomeKit/Yahka Mapping Preview

OBS exposes an experimental read-only planning endpoint for the Yahka/Apple
Home integration:

```text
POST /api/v1/homekit/preview
```

The endpoint generates a reviewable mapping from the existing VISU tree. It does
not create ioBroker states, OBS bindings, Yahka accessories, or KNX fallback
scripts.

After review, OBS exposes a controlled apply endpoint:

```text
POST /api/v1/homekit/apply
```

The default is `dry_run: true`, so the first call returns the import plan without
changing OBS or ioBroker. Treat this API as a migration helper: review the
preview output before running a non-dry-run apply.

## Request

```json
{
  "project": "My Home",
  "root_node_id": "00000000-0000-0000-0000-000000000000",
  "source_visu_name": "Home",
  "leading_iobroker_name": "ioBroker",
  "leading_iobroker_host": "localhost",
  "leading_iobroker_port": 8082,
  "room_strategy": "floor_prefix",
  "accessory_limit_per_bridge": 150,
  "namespace_prefix": "0_userdata.0.obs.home"
}
```

`root_node_id` is optional. If omitted, OBS looks for a VISU root named
`source_visu_name`.

## Output

The response contains:

- normalized ioBroker Home-State IDs
- Apple Home room and accessory names
- per-state `binding_direction`
- OBS binding direction needed for the current adapter model
- status-DP metadata for lights and outlets
- HomeKit bridge limit summary
- system heartbeat states
- backup and restore checklist items

The conceptual binding directions are:

```text
BOTH      writable HomeKit states
FROM_OBS  read-only status states
```

For OBS adapter bindings, `FROM_OBS` maps to the existing adapter direction
`DEST`, because OBS writes the value to ioBroker/Yahka and does not subscribe to
ioBroker changes for that state.

## Current Scope

The preview maps these VISU widgets:

- `Licht` -> `Lightbulb`
- `Toggle` -> `Switch` or `Outlet`
- `Fenster` -> `ContactSensor`
- `Rolladen` -> `WindowCovering`
- `RTR` -> `Thermostat`
- `ValueDisplay` temperature/humidity -> sensor

Unsupported widgets are returned as `Unsupported` with warnings so the mapping
can be reviewed before any productive import is implemented.

## Apply Request

```json
{
  "project": "My Home",
  "root_node_id": "00000000-0000-0000-0000-000000000000",
  "iobroker_instance_id": "11111111-1111-1111-1111-111111111111",
  "dry_run": true,
  "create_iobroker_states": false,
  "room_keys": ["kitchen"],
  "include_unsupported": false
}
```

When `dry_run` is `false`, OBS creates:

- one OBS datapoint per normalized Home-State
- one ioBroker binding per Home-State

The import is idempotent by ioBroker `state_id`: existing bindings for the same
ioBroker instance are skipped.

When `create_iobroker_states` is `true`, the running ioBroker adapter also calls
`setObject` for each state. This requires the selected ioBroker adapter instance
to be connected. Keep this disabled for the first review run.
