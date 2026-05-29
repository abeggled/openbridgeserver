# RingBuffer Metadata Model v1

`metadata_version: 1` beschreibt das Snapshot-Format, das beim Write-Time pro RingBuffer-Eintrag gespeichert wird.

## Struktur

```json
{
  "source": {
    "adapter": "api"
  },
  "datapoint": {
    "id": "uuid",
    "name": "Wohnzimmer Temperatur",
    "data_type": "FLOAT",
    "tags": ["klima", "wohnzimmer"]
  },
  "bindings": [
    {
      "adapter_type": "KNX",
      "adapter_instance_id": "uuid-or-empty",
      "direction": "SOURCE",
      "normalized": {
        "group_address": "1/2/3",
        "state_group_address": "1/2/4",
        "topic": "",
        "entity_id": "",
        "register_type": "",
        "register_address": "",
        "unit_id": ""
      }
    }
  ]
}
```

## Query-v2 Metadata Filter

`POST /api/v1/ringbuffer/query` unterstützt in `filters.metadata`:

- `tags_any_of`
- `adapter_types_any_of`
- `adapter_instance_ids_any_of`
- `group_addresses_any_of`
- `topics_any_of`
- `entity_ids_any_of`
- `register_types_any_of`
- `register_addresses_any_of`

Alle Listen werden case-insensitive normalisiert und gruppenweise per AND verknüpft.
