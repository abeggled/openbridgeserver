"""Stable authorization capability identifiers for Logic side effects."""

LOGIC_NODE_CAPABILITIES = {
    "api_client": "http_request",
    "host_check": "network_probe",
    "ical": "http_request",
    "message_archive": "message_archive",
    "notify_message": "notification",
    "notify_pushover": "notification",
    "notify_sms": "sms",
    "python_script": "python_execution",
    "wake_on_lan": "wake_on_lan",
}

LOGIC_CREATE_CAPABILITY = "create_graph"
"""Closed user-only capability for creating disabled Logic graphs."""

PLUGIN_CAPABILITY = "plugin_execution"
"""Shared capability required to manually run a graph containing any plugin node type.

Plugin node types (obs/logic/plugin_registry.py) are arbitrary third-party code with no
central review, so — unlike built-in nodes — they are never classified individually.
obs/logic/registry.py forces every plugin node type to this one capability, overriding
whatever classification the plugin's own node_type_def() might declare. See
docs/logic-plugin-api.md for the authorization model plugin authors see."""

# Explicit allowlist: a newly registered node is intentionally left
# unclassified and therefore denied by Logic run preflight until reviewed.
PURE_LOGIC_NODE_TYPES = frozenset(
    {
        "ai_logic",
        "and",
        "astro_sun",
        "avg_multi",
        "binary_stats",
        "change_filter",
        "clamp",
        "compare",
        "comment",
        "consumption_counter",
        "const_value",
        "datapoint_read",
        "datapoint_write",
        "datetime",
        "decision",
        "edge_detect",
        "gate",
        "heating_circuit",
        "hems_surplus",
        "hysteresis",
        "json_extractor",
        "math_formula",
        "math_map",
        "memory",
        "merge",
        "min_max_tracker",
        "not",
        "operating_hours",
        "or",
        "random_value",
        "sensor_watchdog",
        "statistics",
        "string_concat",
        "string_replace",
        "substring_extractor",
        "timer_cron",
        "timer_delay",
        "timer_interval",
        "timer_pulse",
        "value_mapping",
        "value_sequence",
        "xml_extractor",
        "xor",
    }
)

LOGIC_CAPABILITIES = frozenset({*LOGIC_NODE_CAPABILITIES.values(), LOGIC_CREATE_CAPABILITY, PLUGIN_CAPABILITY})
