"""Stable authorization capability identifiers for Logic side effects."""

LOGIC_NODE_CAPABILITIES = {
    "api_client": "http_request",
    "host_check": "network_probe",
    "ical": "http_request",
    "notify_pushover": "notification",
    "notify_sms": "sms",
    "python_script": "python_execution",
    "wake_on_lan": "wake_on_lan",
}

# Explicit allowlist: a newly registered node is intentionally left
# unclassified and therefore denied by Logic run preflight until reviewed.
PURE_LOGIC_NODE_TYPES = frozenset(
    {
        "ai_logic",
        "and",
        "astro_sun",
        "avg_multi",
        "clamp",
        "compare",
        "consumption_counter",
        "const_value",
        "datapoint_read",
        "datapoint_write",
        "decision",
        "gate",
        "heating_circuit",
        "hysteresis",
        "json_extractor",
        "math_formula",
        "math_map",
        "memory",
        "min_max_tracker",
        "not",
        "operating_hours",
        "or",
        "random_value",
        "statistics",
        "string_concat",
        "substring_extractor",
        "timer_cron",
        "timer_delay",
        "timer_pulse",
        "value_mapping",
        "xml_extractor",
        "xor",
    }
)

LOGIC_CAPABILITIES = frozenset(LOGIC_NODE_CAPABILITIES.values())
