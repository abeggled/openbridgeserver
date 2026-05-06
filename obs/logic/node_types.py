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
            "value": {"type": "string", "default": "0", "label": "Wert"},
            "data_type": {
                "type": "string",
                "enum": ["number", "bool", "string"],
                "default": "number",
                "label": "Datentyp",
            },
        },
        color="#475569",
    ),
    # ── Logic ────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="and",
        label="AND",
        category="logic",
        description="Ausgang ist true wenn ALLE Eingänge true sind. Eingänge (2–30) und Ausgang einzeln negierbar.",
        inputs=[_port("in1", "IN 1"), _port("in2", "IN 2")],
        outputs=[_port("out", "Out")],
        config_schema={
            "input_count": {
                "type": "number",
                "default": 2,
                "min": 2,
                "max": 30,
                "label": "Anzahl Eingänge",
            },
        },
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="or",
        label="OR",
        category="logic",
        description="Ausgang ist true wenn MINDESTENS EIN Eingang true ist. Eingänge (2–30) und Ausgang einzeln negierbar.",
        inputs=[_port("in1", "IN 1"), _port("in2", "IN 2")],
        outputs=[_port("out", "Out")],
        config_schema={
            "input_count": {
                "type": "number",
                "default": 2,
                "min": 2,
                "max": 30,
                "label": "Anzahl Eingänge",
            },
        },
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="not",
        label="NOT",
        category="logic",
        description="Invertiert den Eingang",
        inputs=[_port("in1", "IN 1")],
        outputs=[_port("out", "Out")],
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="xor",
        label="XOR",
        category="logic",
        description="Ausgang ist true wenn GENAU EIN Eingang true ist. Eingänge (2–30) und Ausgang einzeln negierbar.",
        inputs=[_port("in1", "IN 1"), _port("in2", "IN 2")],
        outputs=[_port("out", "Out")],
        config_schema={
            "input_count": {
                "type": "number",
                "default": 2,
                "min": 2,
                "max": 30,
                "label": "Anzahl Eingänge",
            },
        },
        color="#1d4ed8",
    ),
    NodeTypeDef(
        type="gate",
        label="TOR",
        category="logic",
        description=(
            "Signal-Tor: lässt den Eingang durch wenn Freigabe=true, sperrt sonst. "
            "Verhalten bei gesperrtem Tor: letzten Wert halten (retain) oder Standardwert ausgeben."
        ),
        inputs=[_port("in", "Eingang"), _port("enable", "Freigabe")],
        outputs=[_port("out", "Ausgang")],
        config_schema={
            "closed_behavior": {
                "type": "string",
                "enum": ["retain", "default_value"],
                "default": "retain",
                "label": "Verhalten (gesperrt)",
            },
            "default_value": {
                "type": "string",
                "default": "0",
                "label": "Standardwert (bei gesperrt)",
            },
            "negate_enable": {
                "type": "boolean",
                "default": False,
                "label": "Freigabe invertieren",
            },
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
        },
        color="#1d4ed8",
    ),
    # ── Comparison ────────────────────────────────────────────────────────
    NodeTypeDef(
        type="compare",
        label="Vergleich",
        category="logic",
        description="Vergleicht zwei Werte (>, <, =, >=, <=, !=)",
        inputs=[_port("in1", "IN 1"), _port("in2", "IN 2")],
        outputs=[_port("out", "Ergebnis")],
        config_schema={
            "operator": {
                "type": "string",
                "enum": [">", "<", "=", ">=", "<=", "!="],
                "default": ">",
            },
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
            "threshold_on": {"type": "number", "default": 25.0},
            "threshold_off": {"type": "number", "default": 20.0},
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
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
            "datapoint_id": {"type": "string", "format": "datapoint"},
            "datapoint_name": {"type": "string"},
            # ── Transformation ────────────────────────────────────────────
            "value_formula": {"type": "string", "default": ""},
            # ── Filter ────────────────────────────────────────────────────
            "trigger_on_change": {"type": "boolean", "default": False},
            "min_delta": {"type": "number", "default": ""},
            "min_delta_pct": {"type": "number", "default": ""},
            "throttle_value": {"type": "number", "default": ""},
            "throttle_unit": {"type": "string", "default": "s"},
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
            "datapoint_id": {"type": "string", "format": "datapoint"},
            "datapoint_name": {"type": "string"},
            # ── Transformation ────────────────────────────────────────────
            "value_formula": {"type": "string", "default": ""},
            # ── Filter ────────────────────────────────────────────────────
            "only_on_change": {"type": "boolean", "default": False},
            "min_delta": {"type": "number", "default": ""},
            "throttle_value": {"type": "number", "default": ""},
            "throttle_unit": {"type": "string", "default": "s"},
        },
        color="#0f766e",
    ),
    # ── Math ──────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="math_formula",
        label="Formel",
        category="math",
        description="Berechnet einen Ausdruck. Variablen: a (= IN 1), b (= IN 2)",
        inputs=[_port("in1", "IN 1"), _port("in2", "IN 2")],
        outputs=[_port("result", "Ergebnis")],
        config_schema={
            "formula": {"type": "string", "default": "a + b"},
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
            "in_min": {"type": "number", "default": 0},
            "in_max": {"type": "number", "default": 100},
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
            "min": {"type": "number", "default": 0, "label": "Minimum"},
            "max": {"type": "number", "default": 100, "label": "Maximum"},
        },
        color="#7c3aed",
    ),
    NodeTypeDef(
        type="random_value",
        label="Zufallswert",
        category="math",
        description=(
            "Gibt bei jedem Trigger-Signal einen zufälligen Wert zwischen Min und Max aus. "
            "Typ 'int' liefert eine Ganzzahl (random.randint), "
            "Typ 'float' liefert eine Gleitkommazahl mit konfigurierbaren Nachkommastellen."
        ),
        inputs=[_port("trigger", "Trigger", "trigger")],
        outputs=[_port("value", "Wert")],
        config_schema={
            "data_type": {
                "type": "string",
                "enum": ["int", "float"],
                "default": "int",
                "label": "Datentyp",
            },
            "min": {"type": "number", "default": 0, "label": "Minimum"},
            "max": {"type": "number", "default": 100, "label": "Maximum"},
            "decimal_places": {
                "type": "integer",
                "default": 2,
                "minimum": 0,
                "maximum": 10,
                "label": "Nachkommastellen (nur float)",
            },
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
            _port("min", "Min"),
            _port("max", "Max"),
            _port("avg", "Mittelwert"),
            _port("count", "Anzahl"),
        ],
        config_schema={
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
        },
        color="#7c3aed",
    ),
    # ── Mittelwert / Gleitender Mittelwert ───────────────────────────────
    NodeTypeDef(
        type="avg_multi",
        label="Mittelwert",
        category="math",
        description=(
            "Berechnet den Mittelwert von 2–20 Eingängen (aktuell) und gleitende Mittelwerte "
            "über Zeitfenster: 1 min, 1 h, 1 Tag, 7/14/30/180/365 Tage. "
            "Jeder neue Wert wird mit Zeitstempel gespeichert. "
            "Die Zeitfensterwerte bleiben nach einem Neustart erhalten (konfigurierbar)."
        ),
        inputs=[],  # dynamic — generated by frontend based on input_count
        outputs=[
            _port("avg", "Mittelwert (aktuell)"),
            _port("avg_1m", "Gleit. ∅ 1 min"),
            _port("avg_1h", "Gleit. ∅ 1 Stunde"),
            _port("avg_1d", "Gleit. ∅ 1 Tag"),
            _port("avg_7d", "Gleit. ∅ 7 Tage"),
            _port("avg_14d", "Gleit. ∅ 14 Tage"),
            _port("avg_30d", "Gleit. ∅ 30 Tage"),
            _port("avg_180d", "Gleit. ∅ 180 Tage"),
            _port("avg_365d", "Gleit. ∅ 365 Tage"),
        ],
        config_schema={
            "input_count": {
                "type": "integer",
                "default": 2,
                "min": 2,
                "max": 20,
                "label": "Anzahl Eingänge",
            },
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
        },
        color="#7c3aed",
    ),
    # ── String ───────────────────────────────────────────────────────────
    NodeTypeDef(
        type="string_concat",
        label="String Verketten",
        category="string",
        description="Verkettet 2–20 Texte zu einem Ergebnis. Jeder Eingang kann dynamisch verbunden oder statisch vorbelegt sein. Ist ein Eingang verbunden, hat er Vorrang vor dem statischen Text.",
        inputs=[],  # dynamic — generated by frontend based on count
        outputs=[_port("result", "Ergebnis", "string")],
        config_schema={
            "count": {
                "type": "integer",
                "default": 2,
                "min": 2,
                "max": 20,
                "label": "Anzahl Eingänge",
            },
            "separator": {"type": "string", "default": "", "label": "Trennzeichen"},
        },
        color="#0891b2",
    ),
    # ── Heating Circuit ───────────────────────────────────────────────────
    NodeTypeDef(
        type="heating_circuit",
        label="Sommer/Winter (DIN)",
        category="math",
        description=(
            "Sommer/Winter-Umschaltung nach DIN. Eingang: Aussentemperatur. "
            "Messungen werden exakten Tageszeitpunkten zugeordnet: "
            "T1 = Messung um 07:00 Uhr (Stunde 7), T2 = Messung um 12:00 Uhr (Stunde 12), T3 = Messung um 22:00 Uhr (Stunde 22). "
            "Messungen zu anderen Uhrzeiten werden verworfen. Jeder Slot wird pro Tag nur einmal erfasst. "
            "Tagesmittel: T_avg = (T1 + T2 + 2×T3) / 4. "
            "Monatsmittel: gleitender Mittelwert der letzten 31 Tagesmittel. "
            "Heizmodus ON wenn Mittelwert < Temp. Winter, bleibt ON bis Mittelwert > Temp. Sommer (Hysterese). "
            "Ohne gespeicherte Historik wird der Modus sofort aus der aktuellen Temperatur abgeleitet."
        ),
        inputs=[
            _port("value", "Temp °C"),
        ],
        outputs=[
            _port("heating_mode", "Heizmodus"),
            _port("daily_avg", "Tagesmittel"),
            _port("monthly_avg", "Monatsmittel"),
            _port("t1", "T1 (debug)"),
            _port("t2", "T2 (debug)"),
            _port("t3", "T3 (debug)"),
        ],
        config_schema={
            "temp_winter": {
                "type": "number",
                "default": 15.0,
                "label": "Temp. Winter °C (Heizen EIN)",
            },
            "temp_summer": {
                "type": "number",
                "default": 20.0,
                "label": "Temp. Sommer °C (Heizen AUS)",
            },
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
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
            _port("min_daily", "Min täglich"),
            _port("max_daily", "Max täglich"),
            _port("min_weekly", "Min wöchentlich"),
            _port("max_weekly", "Max wöchentlich"),
            _port("min_monthly", "Min monatlich"),
            _port("max_monthly", "Max monatlich"),
            _port("min_yearly", "Min jährlich"),
            _port("max_yearly", "Max jährlich"),
            _port("min_abs", "Min absolut"),
            _port("max_abs", "Max absolut"),
        ],
        config_schema={
            "init_abs_min": {
                "type": "number",
                "default": None,
                "label": "Startwert Min absolut",
            },
            "init_abs_max": {
                "type": "number",
                "default": None,
                "label": "Startwert Max absolut",
            },
            "init_day_min": {
                "type": "number",
                "default": None,
                "label": "Startwert Min täglich",
            },
            "init_day_max": {
                "type": "number",
                "default": None,
                "label": "Startwert Max täglich",
            },
            "init_month_min": {
                "type": "number",
                "default": None,
                "label": "Startwert Min monatlich",
            },
            "init_month_max": {
                "type": "number",
                "default": None,
                "label": "Startwert Max monatlich",
            },
            "init_year_min": {
                "type": "number",
                "default": None,
                "label": "Startwert Min jährlich",
            },
            "init_year_max": {
                "type": "number",
                "default": None,
                "label": "Startwert Max jährlich",
            },
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
        },
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
            _port("daily", "Täglich"),
            _port("weekly", "Wöchentlich"),
            _port("monthly", "Monatlich"),
            _port("yearly", "Jährlich"),
            _port("prev_daily", "Vorgestern"),
            _port("prev_weekly", "Vorwoche"),
            _port("prev_monthly", "Vormonat"),
            _port("prev_yearly", "Vorjahr"),
        ],
        config_schema={
            "init_meter": {
                "type": "number",
                "default": None,
                "label": "Startwert Zählerstand",
            },
            "init_daily": {
                "type": "number",
                "default": None,
                "label": "Startwert täglich",
            },
            "init_weekly": {
                "type": "number",
                "default": None,
                "label": "Startwert wöchentlich",
            },
            "init_monthly": {
                "type": "number",
                "default": None,
                "label": "Startwert monatlich",
            },
            "init_yearly": {
                "type": "number",
                "default": None,
                "label": "Startwert jährlich",
            },
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
        },
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
        inputs=[
            _port("active", "Aktiv", "trigger"),
            _port("reset", "Reset", "trigger"),
        ],
        outputs=[_port("hours", "Stunden")],
        config_schema={
            "persist_state": {
                "type": "boolean",
                "default": True,
                "label": "Zustand nach Neustart wiederherstellen",
            },
        },
        color="#b45309",
    ),
    # ── Script ────────────────────────────────────────────────────────────
    NodeTypeDef(
        type="python_script",
        label="Python Script",
        category="script",
        description="Führt ein Python-Skript aus. Verfügbar: inputs dict → return value",
        inputs=[_port("in1", "IN 1"), _port("in2", "IN 2"), _port("in3", "IN 3")],
        outputs=[_port("result", "Ergebnis")],
        config_schema={
            "script": {
                "type": "string",
                "default": "# inputs['in1'], inputs['in2']\nresult = inputs.get('in1', 0)",
            },
        },
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
            _port("sunset", "Untergang"),
            _port("is_day", "Tagsüber", "trigger"),
        ],
        config_schema={
            "latitude": {"type": "number", "default": 47.37, "label": "Breitengrad"},
            "longitude": {"type": "number", "default": 8.54, "label": "Längengrad"},
        },
        color="#d97706",
    ),
    # ── Notification ──────────────────────────────────────────────────────
    NodeTypeDef(
        type="notify_pushover",
        label="Pushover",
        category="notification",
        description="Sendet eine Push-Benachrichtigung via Pushover API (api.pushover.net). Wird automatisch ausgelöst wenn eine Nachricht am Eingang ankommt.",
        inputs=[
            _port("trigger", "Trigger"),
            _port("message", "Nachricht"),
            _port("url", "URL"),
            _port("url_title", "URL-Titel"),
            _port("image_url", "Bild-URL"),
        ],
        outputs=[_port("sent", "Gesendet", "trigger")],
        config_schema={
            "app_token": {"type": "string", "default": "", "label": "App-Token"},
            "user_key": {"type": "string", "default": "", "label": "User-Key"},
            "title": {
                "type": "string",
                "default": "open bridge server",
                "label": "Titel",
            },
            "message": {
                "type": "string",
                "default": "",
                "label": "Nachricht (Fallback)",
            },
            "priority": {
                "type": "string",
                "enum": ["-1", "0", "1"],
                "default": "0",
                "label": "Priorität (-1=leise, 0=normal, 1=hoch)",
            },
            "url": {"type": "string", "default": "", "label": "URL (optional)"},
            "url_title": {
                "type": "string",
                "default": "",
                "label": "URL-Titel (optional)",
            },
            "image_url": {
                "type": "string",
                "default": "",
                "label": "Bild-URL (optional)",
            },
        },
        color="#e11d48",
    ),
    NodeTypeDef(
        type="notify_sms",
        label="SMS (seven.io)",
        category="notification",
        description="Sendet eine SMS via seven.io Gateway (gateway.seven.io). Wird automatisch ausgelöst wenn eine Nachricht am Eingang ankommt.",
        inputs=[_port("trigger", "Trigger"), _port("message", "Nachricht")],
        outputs=[_port("sent", "Gesendet", "trigger")],
        config_schema={
            "api_key": {"type": "string", "default": "", "label": "API-Key"},
            "to": {"type": "string", "default": "", "label": "Empfänger (+41…)"},
            "sender": {
                "type": "string",
                "default": "obs",
                "label": "Absender (max 11 Zeichen)",
            },
            "message": {
                "type": "string",
                "default": "",
                "label": "Nachricht (Fallback)",
            },
        },
        color="#e11d48",
    ),
    # ── Integration ───────────────────────────────────────────────────────
    NodeTypeDef(
        type="json_extractor",
        label="JSON Extractor",
        category="integration",
        description="Parst einen JSON-String und extrahiert einen Wert anhand eines Schlüsselpfades (Punkt-Notation, z.B. sensors.temperature). Empfangene Daten werden im Konfigurations-Panel zur Pfad-Auswahl angezeigt.",
        inputs=[_port("data", "Daten")],
        outputs=[_port("value", "Wert")],
        config_schema={
            "json_path": {"type": "string", "default": "", "label": "Schlüsselpfad"},
        },
        color="#0369a1",
    ),
    NodeTypeDef(
        type="xml_extractor",
        label="XML Extractor",
        category="integration",
        description="Parst einen XML-String und extrahiert einen Wert anhand eines XPath-Ausdrucks (ElementTree-Syntax, z.B. ./sensor/temperature). Empfangene Daten werden im Konfigurations-Panel zur Pfad-Auswahl angezeigt.",
        inputs=[_port("data", "Daten")],
        outputs=[_port("value", "Wert")],
        config_schema={
            "xml_path": {"type": "string", "default": "", "label": "XPath-Ausdruck"},
        },
        color="#0369a1",
    ),
    NodeTypeDef(
        type="substring_extractor",
        label="Substring / RegEx",
        category="integration",
        description="Extrahiert Text aus einem String per Substring-Operation oder regulärem Ausdruck. Modi: links_von / rechts_von (erstes oder letztes Vorkommen), zwischen (zwei Markierungen), ausschneiden (Position + Länge), regex (Python re-Syntax, Gruppen wählbar).",
        inputs=[_port("data", "Daten")],
        outputs=[_port("value", "Wert")],
        config_schema={
            "mode": {
                "type": "string",
                "enum": ["links_von", "rechts_von", "zwischen", "ausschneiden", "regex"],
                "default": "rechts_von",
                "label": "Modus",
            },
            "search": {"type": "string", "default": "", "label": "Suchbegriff (links_von / rechts_von)"},
            "occurrence": {"type": "string", "enum": ["first", "last"], "default": "first", "label": "Vorkommen (erstes / letztes)"},
            "start_marker": {"type": "string", "default": "", "label": "Start-Markierung (zwischen)"},
            "end_marker": {"type": "string", "default": "", "label": "End-Markierung (zwischen)"},
            "start": {"type": "number", "default": 0, "label": "Startposition (ausschneiden, 0-basiert)"},
            "length": {"type": "number", "default": -1, "label": "Länge (ausschneiden, -1 = bis Ende)"},
            "pattern": {"type": "string", "default": "", "label": "RegEx-Muster"},
            "flags": {"type": "string", "default": "", "label": "Flags (z.B. i für case-insensitive)"},
            "group": {"type": "number", "default": 0, "label": "Capture-Gruppe (0 = gesamter Treffer)"},
        },
        color="#0369a1",
    ),
    # ── iCalendar ─────────────────────────────────────────────────────────
    NodeTypeDef(
        type="ical",
        label="iCalendar",
        category="integration",
        description=(
            "Lädt ein iCal-/ICS-File von einer URL und wertet Termine aus. "
            "RAW-Ausgang liefert den rohen Kalendertext. "
            "Pro Filter gibt es 4 Ausgänge: Array (alle Termine), Nächstes Datum, Morgen (Bool), Heute (Bool). "
            "Filter können auf Summary, Location und/oder Description angewendet werden."
        ),
        inputs=[],
        outputs=[_port("raw", "RAW")],  # filter outputs are dynamic — generated by frontend/executor
        config_schema={
            "url": {"type": "string", "default": "", "label": "iCal-URL"},
            "refresh_interval_min": {
                "type": "number",
                "default": 60,
                "label": "Aktualisierungsintervall (Minuten)",
            },
            "filter_count": {
                "type": "integer",
                "default": 0,
                "min": 0,
                "max": 20,
                "label": "Anzahl Filter",
            },
            "filters": {
                "type": "string",
                "default": "[]",
                "label": "Filter (JSON)",
            },
        },
        color="#0369a1",
    ),
    NodeTypeDef(
        type="api_client",
        label="API Client",
        category="integration",
        description="Sendet HTTP-Anfragen (GET/POST/PUT…) an externe APIs. Trigger-Eingang steuert die Ausführung.",
        inputs=[_port("trigger", "Trigger", "trigger"), _port("body", "Body")],
        outputs=[
            _port("response", "Antwort"),
            _port("status", "Status"),
            _port("success", "Erfolg", "trigger"),
        ],
        config_schema={
            "url": {"type": "string", "default": "", "label": "URL"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "default": "GET",
                "label": "Methode",
            },
            "content_type": {
                "type": "string",
                "enum": [
                    "application/json",
                    "text/plain",
                    "application/x-www-form-urlencoded",
                ],
                "default": "application/json",
                "label": "Request Content-Type",
            },
            "response_type": {
                "type": "string",
                "enum": ["application/json", "text/plain"],
                "default": "application/json",
                "label": "Response Content-Typ",
            },
            "verify_ssl": {
                "type": "boolean",
                "default": True,
                "label": "SSL-Zertifikat prüfen",
            },
            "headers": {
                "type": "string",
                "default": "",
                "label": "Header (JSON-Objekt, optional)",
            },
            "timeout_s": {"type": "number", "default": 10, "label": "Timeout (s)"},
            "auth_type": {
                "type": "string",
                "enum": ["none", "basic", "digest", "bearer"],
                "default": "none",
                "label": "Authentifizierung",
            },
            "auth_username": {
                "type": "string",
                "default": "",
                "label": "Benutzername (Basic/Digest)",
            },
            "auth_password": {
                "type": "string",
                "default": "",
                "label": "Passwort (Basic/Digest)",
                "subtype": "password",
            },
            "auth_token": {
                "type": "string",
                "default": "",
                "label": "Bearer Token",
                "subtype": "password",
            },
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
