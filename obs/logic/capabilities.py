"""Stable authorization capability identifiers for Logic side effects."""

LOGIC_CAPABILITIES = frozenset(
    {
        "http_request",
        "notification",
        "python_execution",
        "sms",
        "wake_on_lan",
    }
)
