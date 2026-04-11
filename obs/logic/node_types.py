"""Registry of all built-in node type definitions."""
from __future__ import annotations

from obs.logic.models import NodeTypeDef, NodeTypePort

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _port(id_: str, label: str, type_: str = "value") -> NodeTypePort:
    return NodeTypePort(id=id_, label=label, type=type_)


# ---------------------------------------------------------------------------
# Built-in node type definitions
# ---------------------------------------------------------------------------

BUILTIN_NODE_TYPES: list[NodeTypeDef] = [

    # ── Constant ─────────────────────────────────────────────────────────
    NodeTypeDef(
        type="const_value",
        label="Festwert",
        category="logic",
        description="Gibt einen festen Wert aus — Zahl, Bool oder Text. Nützlich als Schwellwert oder Referenz.",
        inputs=[],
        outputs=[_port("value", "Wert")],
        config_schema={
            "value":     {"type": "string", "default": "0",      "label": "Wert"},
            "data_type": {"type": "string", "enum": ["number", "bool", "string"],
                          "default": "number", "label": "Datentyp"},
        },
        color="#475569",
    ),

    # ── Logic ────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="and",
        label="AND",
        category="logic",
        description="Ausgang ist true wenn ALLE Eingänge true sind. Eingänge (2–30) und Ausgang einzeln negierbar.",
        inputs=[_port("a", "A"), _port("b", "B")],
        outputs=[_port("out", "Out")],
        config_schema={
            "input_count": {"type": "number", "default": 2, "min": 2, "max": 30, "label": "Anzahl Eingänge"},
            "negate_a":    {"type": "boolean", "default": False, "label": "Eingang A negieren"},
            "negate_b":    {"type": "boolean", "default": False, "label": "Eingang B negieren"},
            "negate_out":  {"type": "boolean", "default": False, "label": "Ausgang negieren"},
        },
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="or",
        label="OR",
        category="logic",
        description="Ausgang ist true wenn MINDESTENS EIN Eingang true ist. Eingänge (2–30) und Ausgang einzeln negierbar.",
        inputs=[_port("a", "A"), _port("b", "B")],
        outputs=[_port("out", "Out")],
        config_schema={
            "input_count": {"type": "number", "default": 2, "min": 2, "max": 30, "label": "Anzahl Eingänge"},
            "negate_a":    {"type": "boolean", "default": False, "label": "Eingang A negieren"},
            "negate_b":    {"type": "boolean", "default": False, "label": "Eingang B negieren"},
            "negate_out":  {"type": "boolean", "default": False, "label": "Ausgang negieren"},
        },
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="not",
        label="NOT",
        category="logic",
        description="Invertiert den Eingang",
        inputs=[_port("in", "In")],
        outputs=[_port("out", "Out")],
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="xor",
        label="XOR",
        category="logic",
        description="Ausgang ist true wenn GENAU EIN Eingang true ist. Eingänge (2–30) und Ausgang einzeln negierbar.",
        inputs=[_port("a", "A"), _port("b", "B")],
        outputs=[_port("out", "Out")],
        config_schema={
            "input_count": {"type": "number", "default": 2, "min": 2, "max": 30, "label": "Anzahl Eingänge"},
            "negate_a":    {"type": "boolean", "default": False, "label": "Eingang A negieren"},
            "negate_b":    {"type": "boolean", "default": False, "label": "Eingang B negieren"},
            "negate_out":  {"type": "boolean", "default": False, "label": "Ausgang negieren"},
        },
        color="#1d4ed8",
    ),

    # ── Comparison ────────────────────────────────────────────────────────
    NodeTypeDef(
        type="compare",
        label="Vergleich",
        category="logic",
        description="Vergleicht zwei Werte (>, <, =, >=, <=, !=)",
        inputs=[_port("a", "A"), _port("b", "B")],
        outputs=[_port("out", "Ergebnis")],
        config_schema={
            "operator": {"type": "string", "enum": [">", "<", "=", ">=", "<=", "!="], "default": ">"}
        },
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="hysteresis",
        label="Hysterese",
        category="logic",
        description="Schaltet bei Überschreitung ON, erst bei Unterschreitung OFF",
        inputs=[_port("value", "Wert")],
        outputs=[_port("out", "Out")],
        config_schema={
            "threshold_on":  {"type": "number", "default": 25.0},
            "threshold_off": {"type": "number", "default": 20.0},
        },
        color="#1d4ed8",
    ),

    # ── DataPoint ─────────────────────────────────────────────────────────
    NodeTypeDef(
        type="datapoint_read",
        label="Objekt lesen",
        category="datapoint",
        description="Gibt den aktuellen Wert eines DataPoints aus. Triggert bei Wertänderung.",
        inputs=[],
        outputs=[_port("value", "Wert"), _port("changed", "Geändert", "trigger")],
        config_schema={
            "datapoint_id":      {"type": "string", "format": "datapoint"},
            "datapoint_name":    {"type": "string"},
            # ── Transformation ────────────────────────────────────────────
            "value_formula":     {"type": "string",  "default": ""},
            # ── Filter ────────────────────────────────────────────────────
            "trigger_on_change": {"type": "boolean", "default": False},
            "min_delta":         {"type": "number",  "default": ""},
            "min_delta_pct":     {"type": "number",  "default": ""},
            "throttle_value":    {"type": "number",  "default": ""},
            "throttle_unit":     {"type": "string",  "default": "s"},
        },
        color="#0f766e",
    ),
    NodeTypeDef(
        type="datapoint_write",
        label="Objekt schreiben",
        category="datapoint",
        description="Schreibt einen Wert in einen DataPoint",
        inputs=[_port("value", "Wert"), _port("trigger", "Trigger", "trigger")],
        outputs=[],
        config_schema={
            "datapoint_id":   {"type": "string",  "format": "datapoint"},
            "datapoint_name": {"type": "string"},
            # ── Transformation ────────────────────────────────────────────
            "value_formula":  {"type": "string",  "default": ""},
            # ── Filter ────────────────────────────────────────────────────
            "only_on_change": {"type": "boolean", "default": False},
            "min_delta":      {"type": "number",  "default": ""},
            "throttle_value": {"type": "number",  "default": ""},
            "throttle_unit":  {"type": "string",  "default": "s"},
        },
        color="#0f766e",
    ),

    # ── Math ──────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="math_formula",
        label="Formel",
        category="math",
        description="Berechnet einen Ausdruck. Variablen: a, b",
        inputs=[_port("a", "A"), _port("b", "B")],
        outputs=[_port("result", "Ergebnis")],
        config_schema={
            "formula":        {"type": "string", "default": "a + b"},
            "output_formula": {"type": "string", "default": ""},
        },
        color="#7c3aed",
    ),
    NodeTypeDef(
        type="math_map",
        label="Skalieren",
        category="math",
        description="Skaliert einen Wert von einem Bereich in einen anderen",
        inputs=[_port("value", "Wert")],
        outputs=[_port("result", "Ergebnis")],
        config_schema={
            "in_min":  {"type": "number", "default": 0},
            "in_max":  {"type": "number", "default": 100},
            "out_min": {"type": "number", "default": 0},
            "out_max": {"type": "number", "default": 1},
        },
        color="#7c3aed",
    ),
    NodeTypeDef(
        type="clamp",
        label="Begrenzer",
        category="math",
        description="Begrenzt den Eingangswert auf [Min, Max]. Werte außerhalb werden auf den Grenzwert gesetzt.",
        inputs=[_port("value", "Wert")],
        outputs=[_port("result", "Ergebnis")],
        config_schema={
            "min": {"type": "number", "default": 0,   "label": "Minimum"},
            "max": {"type": "number", "default": 100, "label": "Maximum"},
        },
        color="#7c3aed",
    ),
    NodeTypeDef(
        type="statistics",
        label="Statistik",
        category="math",
        description="Berechnet Min/Max/Mittelwert laufend über alle empfangenen Werte. Reset-Eingang setzt zurück.",
        inputs=[_port("value", "Wert"), _port("reset", "Reset", "trigger")],
        outputs=[
            _port("min",   "Min"),
            _port("max",   "Max"),
            _port("avg",   "Mittelwert"),
            _port("count", "Anzahl"),
        ],
        config_schema={},
        color="#7c3aed",
    ),

    # ── Heating Circuit ───────────────────────────────────────────────────
    NodeTypeDef(
        type="heating_circuit",
        label="Heizkreis (DIN)",
        category="math",
        description=(
            "Winter/Sommer-Umschaltung nach DIN. Berechnet Tagesmittel aus drei Messzeitpunkten "
            "(7:00, 14:00, 22:00 Uhr): T_avg = (T1 + T2 + 2×T3) / 4. "
            "Heizmodus aktiv wenn gleitendes Monatsmittel < Heizgrenze."
        ),
        inputs=[
            _port("t1", "T1 (07:00)"),
            _port("t2", "T2 (14:00)"),
            _port("t3", "T3 (22:00)"),
        ],
        outputs=[
            _port("heating_mode", "Heizmodus (0/1)"),
            _port("daily_avg",    "Tagesmittel °C"),
            _port("monthly_avg",  "Monatsmittel °C"),
        ],
        config_schema={
            "heating_limit": {"type": "number", "default": 15.0, "label": "Heizgrenze °C"},
        },
        color="#7c3aed",
    ),

    # ── Min/Max Tracker ───────────────────────────────────────────────────
    NodeTypeDef(
        type="min_max_tracker",
        label="Min/Max Tracker",
        category="math",
        description=(
            "Verfolgt Minimum und Maximum über Zeitperioden "
            "(täglich, wöchentlich, monatlich, jährlich, absolut). "
            "Periodenwerte werden automatisch am Tages-/Wochen-/Monats-/Jahreswechsel zurückgesetzt."
        ),
        inputs=[_port("value", "Wert")],
        outputs=[
            _port("min_daily",   "Min täglich"),
            _port("max_daily",   "Max täglich"),
            _port("min_weekly",  "Min wöchentlich"),
            _port("max_weekly",  "Max wöchentlich"),
            _port("min_monthly", "Min monatlich"),
            _port("max_monthly", "Max monatlich"),
            _port("min_yearly",  "Min jährlich"),
            _port("max_yearly",  "Max jährlich"),
            _port("min_abs",     "Min absolut"),
            _port("max_abs",     "Max absolut"),
        ],
        config_schema={},
        color="#7c3aed",
    ),

    # ── Consumption Counter ───────────────────────────────────────────────
    NodeTypeDef(
        type="consumption_counter",
        label="Verbrauchszähler",
        category="math",
        description=(
            "Berechnet Verbrauchswerte (täglich, wöchentlich, monatlich, jährlich) "
            "aus einem fortlaufenden Zählerwert. "
            "Speichert zusätzlich den Verbrauch der Vorperiode für Vergleiche."
        ),
        inputs=[_port("value", "Zählerwert")],
        outputs=[
            _port("daily",        "Täglich"),
            _port("weekly",       "Wöchentlich"),
            _port("monthly",      "Monatlich"),
            _port("yearly",       "Jährlich"),
            _port("prev_daily",   "Vorgestern"),
            _port("prev_weekly",  "Vorwoche"),
            _port("prev_monthly", "Vormonat"),
            _port("prev_yearly",  "Vorjahr"),
        ],
        config_schema={},
        color="#7c3aed",
    ),

    # ── Timer ─────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="timer_delay",
        label="Verzögerung",
        category="timer",
        description="Verzögert ein Signal um N Sekunden",
        inputs=[_port("trigger", "Trigger", "trigger")],
        outputs=[_port("trigger", "Trigger", "trigger")],
        config_schema={"delay_s": {"type": "number", "default": 1.0}},
        color="#b45309",
    ),
    NodeTypeDef(
        type="timer_pulse",
        label="Impuls",
        category="timer",
        description="Gibt einen Impuls für N Sekunden aus",
        inputs=[_port("trigger", "Trigger", "trigger")],
        outputs=[_port("out", "Out")],
        config_schema={"duration_s": {"type": "number", "default": 1.0}},
        color="#b45309",
    ),
    NodeTypeDef(
        type="timer_cron",
        label="Trigger",
        category="timer",
        description="Löst automatisch nach einem Cron-Zeitplan aus (Minute Stunde Tag Monat Wochentag).",
        inputs=[],
        outputs=[_port("trigger", "Trigger", "trigger")],
        config_schema={"cron": {"type": "string", "default": "0 7 * * *"}},
        color="#b45309",
    ),
    NodeTypeDef(
        type="operating_hours",
        label="Betriebsstunden",
        category="timer",
        description="Zählt Betriebsstunden solange 'Aktiv' wahr ist. Reset setzt den Zähler zurück.",
        inputs=[_port("active", "Aktiv", "trigger"), _port("reset", "Reset", "trigger")],
        outputs=[_port("hours", "Stunden")],
        config_schema={},
        color="#b45309",
    ),

    # ── Script ────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="python_script",
        label="Python Script",
        category="script",
        description="Führt ein Python-Skript aus. Verfügbar: inputs dict → return value",
        inputs=[_port("a", "A"), _port("b", "B"), _port("c", "C")],
        outputs=[_port("result", "Ergebnis")],
        config_schema={"script": {"type": "string", "default": "# inputs['a'], inputs['b']\nresult = inputs.get('a', 0)"}},
        color="#be185d",
    ),

    # ── AI ────────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="ai_logic",
        label="AI Logic",
        category="ai",
        description="",
        inputs=[],
        outputs=[],
        config_schema={},
        color="#7c3aed",
    ),

    # ── Astro ─────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="astro_sun",
        label="Astro Sonne",
        category="astro",
        description="Berechnet Sonnenauf- und -untergang basierend auf Breitengrad/Längengrad. Benötigt: pip install astral",
        inputs=[],
        outputs=[
            _port("sunrise", "Aufgang"),
            _port("sunset",  "Untergang"),
            _port("is_day",  "Tagsüber", "trigger"),
        ],
        config_schema={
            "latitude":  {"type": "number", "default": 47.37, "label": "Breitengrad"},
            "longitude": {"type": "number", "default": 8.54,  "label": "Längengrad"},
        },
        color="#d97706",
    ),

    # ── Notification ──────────────────────────────────────────────────────
    NodeTypeDef(
        type="notify_pushover",
        label="Pushover",
        category="notification",
        description="Sendet eine Push-Benachrichtigung via Pushover API (api.pushover.net).",
        inputs=[_port("trigger", "Trigger", "trigger"), _port("message", "Nachricht")],
        outputs=[_port("sent", "Gesendet", "trigger")],
        config_schema={
            "app_token": {"type": "string", "default": "", "label": "App-Token"},
            "user_key":  {"type": "string", "default": "", "label": "User-Key"},
            "title":     {"type": "string", "default": "open bridge server", "label": "Titel"},
            "message":   {"type": "string", "default": "", "label": "Nachricht (Fallback)"},
            "priority":  {
                "type": "string",
                "enum": ["-1", "0", "1"],
                "default": "0",
                "label": "Priorität (-1=leise, 0=normal, 1=hoch)",
            },
        },
        color="#e11d48",
    ),
    NodeTypeDef(
        type="notify_sms",
        label="SMS (seven.io)",
        category="notification",
        description="Sendet eine SMS via seven.io Gateway (gateway.seven.io).",
        inputs=[_port("trigger", "Trigger", "trigger"), _port("message", "Nachricht")],
        outputs=[_port("sent", "Gesendet", "trigger")],
        config_schema={
            "api_key": {"type": "string", "default": "", "label": "API-Key"},
            "to":      {"type": "string", "default": "", "label": "Empfänger (+41…)"},
            "sender":  {"type": "string", "default": "open bridge server", "label": "Absender"},
            "message": {"type": "string", "default": "", "label": "Nachricht (Fallback)"},
        },
        color="#e11d48",
    ),

    # ── Integration ───────────────────────────────────────────────────────
    NodeTypeDef(
        type="api_client",
        label="API Client",
        category="integration",
        description="Sendet HTTP-Anfragen (GET/POST/PUT…) an externe APIs. Trigger-Eingang steuert die Ausführung.",
        inputs=[_port("trigger", "Trigger", "trigger"), _port("body", "Body")],
        outputs=[
            _port("response", "Antwort"),
            _port("status",   "Status"),
            _port("success",  "Erfolg", "trigger"),
        ],
        config_schema={
            "url":           {"type": "string", "default": "",    "label": "URL"},
            "method":        {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET", "label": "Methode"},
            "content_type":  {"type": "string", "enum": ["application/json", "text/plain", "application/x-www-form-urlencoded"], "default": "application/json", "label": "Request Content-Type"},
            "response_type": {"type": "string", "enum": ["json", "text"], "default": "json", "label": "Response-Content-Typ"},
            "verify_ssl":    {"type": "boolean", "default": True,  "label": "SSL-Zertifikat prüfen"},
            "headers":       {"type": "string",  "default": "",    "label": "Header (JSON-Objekt, optional)"},
            "timeout_s":     {"type": "number",  "default": 10,    "label": "Timeout (s)"},
        },
        color="#0e7490",
    ),
]

# Dict lookup by type
NODE_TYPE_REGISTRY: dict[str, NodeTypeDef] = {nt.type: nt for nt in BUILTIN_NODE_TYPES}


def get_node_type(type_: str) -> NodeTypeDef | None:
    return NODE_TYPE_REGISTRY.get(type_)


def list_node_types() -> list[NodeTypeDef]:
    return BUILTIN_NODE_TYPES
