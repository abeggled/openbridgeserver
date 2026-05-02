"""Zeitschaltuhr Adapter — Tages- und Jahresschaltuhr

Nur SOURCE-Bindings sind gültig (Zeitschaltuhr ist eine reine Quelle).
DEST- und BOTH-Bindings werden beim Laden ignoriert (mit Warnung).

Adapter-Konfiguration:
  latitude:             float  — Breitengrad für Sonnenauf/-untergang (default: 47.5)
  longitude:            float  — Längengrad (default: 8.0)
  altitude:             float  — Höhe ü.M. in Metern (default: 400)
  timezone:             str    — Zeitzone (leer = App-Zeitzone, z.B. "Europe/Zurich")
  holiday_country:      str    — ISO 3166-1 Ländercode (DE, AT, CH; default: CH)
  holiday_subdivision:  str    — Kanton/Bundesland (z.B. ZH, BY) — leer = Bundesfeiertage
  vacation_1_start ... vacation_6_end:  str — Ferienperioden als JJJJ-MM-TT

Binding-Konfiguration (1 Binding = 1 Schaltpunkt):
  ─── Typ ────────────────────────────────────────────────────────────────────
  timer_type:   "daily" | "annual" | "meta"
    "daily"  — Tagesschaltuhr: täglich/wöchentlich
    "annual" — Jahresschaltuhr: monatlich/jährlich
    "meta"   — Metadaten-Binding: publiziert Feiertag-/Ferienstatus automatisch

  meta_type:  (nur bei timer_type="meta")
    "none" | "holiday_today" | "holiday_tomorrow" |
    "holiday_name_today" | "holiday_name_tomorrow" |
    "vacation_1" .. "vacation_6"

  ─── Wochentag / Datum ───────────────────────────────────────────────────────
  weekdays:       Liste aktiver Wochentage [0=Mo .. 6=So] (default: alle)
  months:         Liste aktiver Monate [1-12] (leer = alle; nur für annual)
  day_of_month:   Tag im Monat 0-31 (0 = alle; nur für annual)

  ─── Zeitreferenz ────────────────────────────────────────────────────────────
  time_ref:             "absolute" | "sunrise" | "sunset" | "solar_noon" | "solar_altitude"
  hour:                 0-23  (nur bei time_ref=absolute)
  minute:               0-59
  offset_minutes:       Offset in Minuten (positiv/negativ) relativ zur Zeitreferenz
  solar_altitude_deg:   Sonnenhöhenwinkel in Grad (−18 … 90; nur bei time_ref=solar_altitude)
  sun_direction:        "rising" | "setting"  (nur bei time_ref=solar_altitude)

  ─── Takt ────────────────────────────────────────────────────────────────────
  every_hour:     bool — jede Stunde zur angegebenen Minute schalten
  every_minute:   bool — jede Minute schalten

  ─── Feiertag / Ferien ───────────────────────────────────────────────────────
  holiday_mode:   "ignore" | "skip" | "only" | "as_sunday"
  vacation_mode:  "ignore" | "skip" | "only" | "as_sunday"
    ignore    — Feiertage/Ferien wie Normaltage behandeln
    skip      — Nicht schalten an Feiertagen/Ferientagen
    only      — Nur an Feiertagen/Ferientagen schalten
    as_sunday — Feiertage/Ferientage wie Sonntag behandeln

  ─── Ausgabe ─────────────────────────────────────────────────────────────────
  value:  Ausgabewert beim Schalten (default: "1")
          Gültige Werte: "0"/"1", "true"/"false", "on"/"off", "ein"/"aus",
          Ganzzahl, Dezimalzahl oder beliebiger String
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom holiday helpers
# ---------------------------------------------------------------------------

_WEEKDAY_ABBR: dict[str, int] = {
    "MO": 0,
    "DI": 1,
    "MI": 2,
    "DO": 3,
    "FR": 4,
    "SA": 5,
    "SO": 6,
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}
_MONTH_ABBR: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "MÄR": 3,
    "APR": 4,
    "MAY": 5,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OKT": 10,
    "OCT": 10,
    "NOV": 11,
    "DEZ": 12,
    "DEC": 12,
}


def _advent1_date(year: int) -> date:
    """Returns the 1st Advent Sunday for the given year.

    1. Advent is always the 4th Sunday before Christmas (Dec 25),
    i.e. the last Sunday of November/first Sunday of December (Nov 27 – Dec 3).
    """
    dec25 = date(year, 12, 25)
    # weekday(): 0=Mon … 6=Sun  →  days to step back to reach Sunday before Dec 25
    days_back = dec25.weekday() + 1  # Mon→1, Tue→2, …, Sun→7
    advent4 = dec25 - timedelta(days=days_back)
    return advent4 - timedelta(days=21)


def _easter_date(year: int) -> date:
    """Computes Easter Sunday for the given year (Gregorian calendar, Anonymous algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    lv = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lv) // 451
    month = (h + lv - 7 * m + 114) // 31
    day = ((h + lv - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Returns the last occurrence of `weekday` (0=Mon…6=Sun) in the given month."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date | None:
    """Returns the n-th occurrence (1-based) of `weekday` in the given month, or None if out of range."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    target = first + timedelta(days=offset + 7 * (n - 1))
    return target if target.month == month else None


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TimerType(str, Enum):
    DAILY = "daily"
    ANNUAL = "annual"
    META = "meta"
    HOLIDAY = "holiday"


class TimeRef(str, Enum):
    ABSOLUTE = "absolute"
    SUNRISE = "sunrise"
    SUNSET = "sunset"
    SOLAR_NOON = "solar_noon"
    SOLAR_ALTITUDE = "solar_altitude"


class SunDirection(str, Enum):
    RISING = "rising"
    SETTING = "setting"


class HolidayMode(str, Enum):
    IGNORE = "ignore"
    SKIP = "skip"
    ONLY = "only"
    AS_SUNDAY = "as_sunday"


class MetaType(str, Enum):
    NONE = "none"
    HOLIDAY_TODAY = "holiday_today"
    HOLIDAY_TOMORROW = "holiday_tomorrow"
    HOLIDAY_NAME_TODAY = "holiday_name_today"
    HOLIDAY_NAME_TOMORROW = "holiday_name_tomorrow"
    VACATION_1 = "vacation_1"
    VACATION_2 = "vacation_2"
    VACATION_3 = "vacation_3"
    VACATION_4 = "vacation_4"
    VACATION_5 = "vacation_5"
    VACATION_6 = "vacation_6"


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class ZeitschaltuhrConfig(BaseModel):
    latitude: float = Field(47.5, title="Breitengrad", description="Breitengrad für Sonnenauf/-untergang")
    longitude: float = Field(8.0, title="Längengrad", description="Längengrad für Sonnenauf/-untergang")
    altitude: float = Field(400.0, title="Höhe ü.M. (m)", description="Höhe ü.M. in Metern")
    timezone: str = Field("", title="Zeitzone", description="Zeitzone (leer = App-Zeitzone, z.B. Europe/Zurich)")
    holiday_country: str = Field("CH", title="Feiertagsland", description="Feiertagsland (ISO 3166-1: DE, AT, CH, FR …)")
    holiday_subdivision: str = Field(
        "",
        title="Kanton / Bundesland",
        description="Kanton/Bundesland (z.B. ZH, BY, OÖ) — leer = nur nationale Feiertage",
    )
    holiday_language: str = Field(
        "de",
        title="Feiertagssprache",
        description="Sprache der Feiertagsnamen (de, fr, it, en) — Standard: de",
    )
    vacation_1_start: str = Field("", title="Ferien 1 Beginn", description="Ferienperiode 1 Beginn (JJJJ-MM-TT)")
    vacation_1_end: str = Field("", title="Ferien 1 Ende", description="Ferienperiode 1 Ende (JJJJ-MM-TT)")
    vacation_2_start: str = Field("", title="Ferien 2 Beginn", description="Ferienperiode 2 Beginn (JJJJ-MM-TT)")
    vacation_2_end: str = Field("", title="Ferien 2 Ende", description="Ferienperiode 2 Ende (JJJJ-MM-TT)")
    vacation_3_start: str = Field("", title="Ferien 3 Beginn", description="Ferienperiode 3 Beginn (JJJJ-MM-TT)")
    vacation_3_end: str = Field("", title="Ferien 3 Ende", description="Ferienperiode 3 Ende (JJJJ-MM-TT)")
    vacation_4_start: str = Field("", title="Ferien 4 Beginn", description="Ferienperiode 4 Beginn (JJJJ-MM-TT)")
    vacation_4_end: str = Field("", title="Ferien 4 Ende", description="Ferienperiode 4 Ende (JJJJ-MM-TT)")
    vacation_5_start: str = Field("", title="Ferien 5 Beginn", description="Ferienperiode 5 Beginn (JJJJ-MM-TT)")
    vacation_5_end: str = Field("", title="Ferien 5 Ende", description="Ferienperiode 5 Ende (JJJJ-MM-TT)")
    vacation_6_start: str = Field("", title="Ferien 6 Beginn", description="Ferienperiode 6 Beginn (JJJJ-MM-TT)")
    vacation_6_end: str = Field("", title="Ferien 6 Ende", description="Ferienperiode 6 Ende (JJJJ-MM-TT)")
    custom_holidays: list[str] = Field(
        default=[],
        description=(
            "Benutzerdefinierte Feiertage als Liste von Ausdrücken. "
            "Formate: "
            "'MM-TT[:Name]' (fixes Datum, z.B. '12-26:Stephanstag'), "
            "'easter+N[:Name]' / 'easter-N[:Name]' (relativ zu Ostersonntag, z.B. 'easter+1:Ostermontag', 'easter-47:Rosenmontag'), "
            "'advent+N[:Name]' / 'advent-N[:Name]' (relativ zum 1. Advent, z.B. 'advent+0:1. Advent', 'advent+13:Heiligabend'), "
            "'last_WT_MON[:Name]' (letzter Wochentag des Monats, z.B. 'last_SO_NOV:Buß+Bettag'), "
            "'N_WT_MON[:Name]' (n-ter Wochentag, z.B. '2_SO_OKT:Erntedank'). "
            "Wochentags-Kürzel: MO DI MI DO FR SA SO (dt.) oder MON TUE WED THU FRI SAT SUN (en.). "
            "Monats-Kürzel: JAN FEB MAR APR MAI JUN JUL AUG SEP OKT NOV DEZ. "
            "Mehrere Einträge durch Komma oder Zeilenumbruch trennen."
        ),
    )

    @field_validator("custom_holidays", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: Any) -> list[str]:
        """Accept a plain string (e.g. from the generic config UI) by splitting on commas/newlines."""
        if isinstance(v, str):
            return [item.strip() for item in v.replace("\n", ",").split(",") if item.strip()]
        return v


class ZeitschaltuhrBindingConfig(BaseModel):
    # ── Typ ──────────────────────────────────────────────────────────────────
    timer_type: TimerType = Field(TimerType.DAILY, description="Schaltuhrentyp")
    meta_type: MetaType = Field(
        MetaType.NONE,
        description="Metadaten-Typ (nur bei timer_type=meta)",
    )

    # ── Wochentag / Datum ─────────────────────────────────────────────────────
    weekdays: list[int] = Field(
        default=[0, 1, 2, 3, 4, 5, 6],
        description="Aktive Wochentage (0=Mo, 1=Di, 2=Mi, 3=Do, 4=Fr, 5=Sa, 6=So)",
    )
    months: list[int] = Field(
        default=[],
        description="Aktive Monate 1-12 (leer = alle; nur Jahresschaltuhr)",
    )
    day_of_month: int = Field(
        0,
        ge=0,
        le=31,
        description="Tag im Monat (0 = alle; nur Jahresschaltuhr)",
    )

    # ── Zeitreferenz ──────────────────────────────────────────────────────────
    time_ref: TimeRef = Field(TimeRef.ABSOLUTE, description="Zeitreferenz")
    hour: int = Field(0, ge=0, le=23, description="Stunde (nur bei time_ref=absolute)")
    minute: int = Field(0, ge=0, le=59, description="Minute")
    offset_minutes: int = Field(
        0,
        description="Offset in Minuten (positiv/negativ) relativ zur Zeitreferenz",
    )
    solar_altitude_deg: float = Field(
        0.0,
        ge=-18.0,
        le=90.0,
        description=(
            "Sonnenhöhenwinkel in Grad (−18 … 90) — nur bei time_ref=solar_altitude. "
            "Beispiele: 0° = Horizont, −6° = bürgerliche Dämmerung, 30° = mittlerer Vormittag"
        ),
    )
    sun_direction: SunDirection = Field(
        SunDirection.RISING,
        description=(
            "Richtung der Sonnenbewegung — nur bei time_ref=solar_altitude. "
            "rising = Sonne steigt (morgens), setting = Sonne sinkt (nachmittags/abends)"
        ),
    )

    # ── Takt ──────────────────────────────────────────────────────────────────
    every_hour: bool = Field(False, description="Jede Stunde zur angegebenen Minute schalten")
    every_minute: bool = Field(False, description="Jede Minute schalten")

    # ── Feiertag / Ferien ─────────────────────────────────────────────────────
    holiday_mode: HolidayMode = Field(HolidayMode.IGNORE, description="Feiertagsbehandlung")
    vacation_mode: HolidayMode = Field(HolidayMode.IGNORE, description="Ferienbehandlung")

    # ── Feiertagsschaltuhr ────────────────────────────────────────────────────
    selected_holidays: list[str] = Field(
        default=[],
        description="Ausgewählte Feiertage für timer_type=holiday (leer = alle Feiertage)",
    )

    # ── Datum-Fenster ─────────────────────────────────────────────────────────
    date_window_enabled: bool = Field(False, description="Datum-Fenster: nur innerhalb eines Zeitraums schalten")
    date_window_from: str = Field("", description="Fenster-Beginn (MM-TT | easter±N | advent±N | holiday:Name±N)")
    date_window_to: str = Field("", description="Fenster-Ende (MM-TT | easter±N | advent±N | holiday:Name±N)")

    # ── Ausgabe ───────────────────────────────────────────────────────────────
    value: str = Field(
        "1",
        description=("Ausgabewert beim Schalten. Erlaubt: 0/1, true/false, on/off, ein/aus, Ganzzahl, Dezimalzahl oder String"),
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class ZeitschaltuhrAdapter(AdapterBase):
    """Zeitschaltuhr (Timer) Adapter — virtuelle Schaltuhr ohne physische Verbindung.

    Nur SOURCE-Bindings sind gültig. DEST- und BOTH-Bindings werden beim
    Laden ignoriert (mit Warnung im Log).

    Unterstützt:
    - Tagesschaltuhr (daily): täglich / nach Wochentagen
    - Jahresschaltuhr (annual): monatlich / nach Monat+Tag
    - Metadaten (meta): Feiertag- / Ferienstatus automatisch publizieren
    - Zeitreferenzen: absolute Zeit, Sonnenaufgang, -untergang, Sonnenhöhenwinkel
    - Feiertag- und Ferienbehandlung: ignorieren / überspringen / nur / als Sonntag
    - Zyklen: jede Minute / jede Stunde
    """

    adapter_type = "ZEITSCHALTUHR"
    config_schema = ZeitschaltuhrConfig
    binding_config_schema = ZeitschaltuhrBindingConfig

    def __init__(
        self,
        event_bus: Any,
        config: dict | None = None,
        instance_id: Any = None,
        name: str | None = None,
    ) -> None:
        super().__init__(event_bus, config, instance_id, name)
        self._cfg = ZeitschaltuhrConfig(**(config or {}))
        self._tz: Any = UTC
        self._task: asyncio.Task | None = None
        self._hol: dict[date, str] = {}
        self._last_date: date | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._tz = await self._resolve_timezone()
        self._hol = self._build_holidays()
        self._connected = True
        await self._publish_status(True, "Zeitschaltuhr gestartet")
        self._task = asyncio.create_task(
            self._timer_loop(),
            name=f"zeitschaltuhr_{self._instance_id}",
        )
        logger.info(
            "Zeitschaltuhr '%s' gestartet (TZ=%s, Land=%s%s)",
            self._instance_name,
            self._tz,
            self._cfg.holiday_country,
            f"/{self._cfg.holiday_subdivision}" if self._cfg.holiday_subdivision else "",
        )

    async def disconnect(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False
        await self._publish_status(False, "Zeitschaltuhr gestoppt")

    async def reload_bindings(self, bindings: list[Any]) -> None:
        """Überschreibt Base-Methode: filtert ungültige Richtungen vor dem Speichern."""
        valid = []
        for b in bindings:
            if b.direction != "SOURCE":
                logger.warning(
                    "Zeitschaltuhr '%s': Binding %s hat Richtung '%s' — nur SOURCE ist erlaubt, Binding wird ignoriert.",
                    self._instance_name,
                    b.id,
                    b.direction,
                )
            else:
                valid.append(b)
        await super().reload_bindings(valid)

    async def _on_bindings_reloaded(self) -> None:
        if self._connected:
            now_local = datetime.now(self._tz)
            await self._publish_meta_bindings(now_local)

    # ------------------------------------------------------------------
    # Data exchange (push-only — writes are rejected silently)
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        return None

    async def write(self, binding: Any, value: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Timer loop
    # ------------------------------------------------------------------

    async def _timer_loop(self) -> None:
        """Wacht auf Minutengrenzen und prüft/feuert alle Schaltpunkte."""
        now_local = datetime.now(self._tz)
        await self._publish_meta_bindings(now_local)
        self._last_date = now_local.date()

        while True:
            try:
                # Sleep to next full minute (+0.1 s overshoot to avoid same-second re-fire)
                now_local = datetime.now(self._tz)
                sleep_secs = 60 - now_local.second + 0.1
                await asyncio.sleep(sleep_secs)

                now_local = datetime.now(self._tz)
                today = now_local.date()

                # Rebuild holidays and refresh meta on day change
                if today != self._last_date:
                    self._hol = self._build_holidays()
                    await self._publish_meta_bindings(now_local)
                    self._last_date = today

                for binding in list(self._bindings):
                    try:
                        cfg = ZeitschaltuhrBindingConfig(**binding.config)
                        if cfg.timer_type not in (TimerType.META,) and self._should_fire(cfg, now_local):
                            await self._fire_binding(binding, cfg)
                    except Exception:
                        logger.exception("Fehler beim Prüfen von Binding %s", binding.id)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unerwarteter Fehler in der Zeitschaltuhr-Schleife")
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Fire logic
    # ------------------------------------------------------------------

    def _should_fire(self, cfg: ZeitschaltuhrBindingConfig, now: datetime) -> bool:
        today = now.date()

        # Date window gate (DAILY / ANNUAL / HOLIDAY — checked before all other gates)
        if cfg.date_window_enabled and cfg.date_window_from and cfg.date_window_to:
            if not self._in_date_window(cfg.date_window_from, cfg.date_window_to, today):
                return False

        # Feiertagsschaltuhr: fires on specific holidays only
        if cfg.timer_type == TimerType.HOLIDAY:
            holiday_name = self._hol.get(today)
            if holiday_name is None:
                return False
            if cfg.selected_holidays and holiday_name not in cfg.selected_holidays:
                return False
            is_vacation = self._is_vacation(today)
            if is_vacation and cfg.vacation_mode == HolidayMode.SKIP:
                return False
            if not is_vacation and cfg.vacation_mode == HolidayMode.ONLY:
                return False
            if cfg.every_minute:
                return True
            if cfg.every_hour:
                return now.minute == cfg.minute
            target = self._calculate_target_time(cfg, today)
            if target is None:
                return False
            return now.hour == target.hour and now.minute == target.minute

        is_holiday = self._is_holiday(today)
        is_vacation = self._is_vacation(today)

        # Effective weekday — may be promoted to Sunday by as_sunday mode
        effective_weekday = now.weekday()
        if is_holiday and cfg.holiday_mode == HolidayMode.AS_SUNDAY:
            effective_weekday = 6
        if is_vacation and cfg.vacation_mode == HolidayMode.AS_SUNDAY:
            effective_weekday = 6

        # Holiday gate
        if is_holiday:
            if cfg.holiday_mode == HolidayMode.SKIP:
                return False
        elif cfg.holiday_mode == HolidayMode.ONLY:
            return False

        # Vacation gate
        if is_vacation:
            if cfg.vacation_mode == HolidayMode.SKIP:
                return False
        elif cfg.vacation_mode == HolidayMode.ONLY:
            return False

        # Weekday gate
        if effective_weekday not in cfg.weekdays:
            return False

        # Annual: month / day-of-month filter
        if cfg.timer_type == TimerType.ANNUAL:
            if cfg.months and now.month not in cfg.months:
                return False
            if cfg.day_of_month and now.day != cfg.day_of_month:
                return False

        # Cycling shortcuts (weekday/month filters still apply above)
        if cfg.every_minute:
            return True
        if cfg.every_hour:
            return now.minute == cfg.minute

        # Compute target time and compare
        target = self._calculate_target_time(cfg, today)
        if target is None:
            return False
        return now.hour == target.hour and now.minute == target.minute

    def _calculate_target_time(self, cfg: ZeitschaltuhrBindingConfig, for_date: date) -> datetime | None:
        if cfg.time_ref == TimeRef.ABSOLUTE:
            base: datetime | None = datetime(
                for_date.year,
                for_date.month,
                for_date.day,
                cfg.hour,
                cfg.minute,
                tzinfo=self._tz,
            )
        elif cfg.time_ref == TimeRef.SOLAR_ALTITUDE:
            base = self._get_solar_altitude_time(cfg.solar_altitude_deg, cfg.sun_direction, for_date)
        else:
            base = self._get_sun_event(cfg.time_ref, for_date)

        if base is None:
            return None
        return base + timedelta(minutes=cfg.offset_minutes)

    def _get_sun_event(self, ref: TimeRef, for_date: date) -> datetime | None:
        """Sonnenaufgang, -untergang oder Sonnenmittag via astral."""
        try:
            from astral import LocationInfo
            from astral.sun import sun

            location = LocationInfo(
                name="open bridge server",
                region="",
                timezone=str(self._tz),
                latitude=self._cfg.latitude,
                longitude=self._cfg.longitude,
            )
            s = sun(location.observer, date=for_date, tzinfo=self._tz)
            return s.get({"sunrise": "sunrise", "sunset": "sunset", "solar_noon": "noon"}[ref.value])
        except ImportError:
            logger.warning("astral nicht installiert — Sonnenzeit-Berechnungen nicht verfügbar (pip install astral)")
        except Exception as exc:
            logger.warning("Sonnenzeit-Berechnung für %s fehlgeschlagen: %s", for_date, exc)
        return None

    def _get_solar_altitude_time(
        self,
        altitude_deg: float,
        direction: SunDirection,
        for_date: date,
    ) -> datetime | None:
        """Zeitpunkt, zu dem die Sonne den angegebenen Höhenwinkel erreicht."""
        try:
            from astral import LocationInfo
            from astral import SunDirection as AstralDir
            from astral.sun import time_at_elevation

            location = LocationInfo(
                name="open bridge server",
                region="",
                timezone=str(self._tz),
                latitude=self._cfg.latitude,
                longitude=self._cfg.longitude,
            )
            astral_dir = AstralDir.RISING if direction == SunDirection.RISING else AstralDir.SETTING
            result = time_at_elevation(
                location.observer,
                elevation=altitude_deg,
                date=for_date,
                direction=astral_dir,
                tzinfo=self._tz,
            )
            return result
        except ImportError:
            logger.warning("astral nicht installiert — Sonnenhöhenwinkel-Berechnung nicht verfügbar (pip install astral)")
        except Exception as exc:
            logger.warning(
                "Sonnenhöhenwinkel-Berechnung für %s°/%s am %s fehlgeschlagen: %s",
                altitude_deg,
                direction.value,
                for_date,
                exc,
            )
        return None

    # ------------------------------------------------------------------
    # Publish helper
    # ------------------------------------------------------------------

    async def _fire_binding(self, binding: Any, cfg: ZeitschaltuhrBindingConfig) -> None:
        from obs.core.event_bus import DataValueEvent

        raw = cfg.value.strip()
        if raw.lower() in ("true", "1", "on", "ein"):
            value: Any = True
        elif raw.lower() in ("false", "0", "off", "aus"):
            value = False
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw

        logger.debug(
            "Zeitschaltuhr '%s': Binding %s → %r",
            self._instance_name,
            binding.id,
            value,
        )
        await self._bus.publish(
            DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=value,
                quality="good",
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            ),
        )

    # ------------------------------------------------------------------
    # Meta data bindings
    # ------------------------------------------------------------------

    async def _publish_meta_bindings(self, now: datetime) -> None:
        """Publiziert Feiertag-/Ferienstatus auf alle META-Bindings."""
        today = now.date()
        tomorrow = today + timedelta(days=1)

        meta_values: dict[MetaType, Any] = {
            MetaType.HOLIDAY_TODAY: self._is_holiday(today),
            MetaType.HOLIDAY_TOMORROW: self._is_holiday(tomorrow),
            MetaType.HOLIDAY_NAME_TODAY: self._holiday_name(today),
            MetaType.HOLIDAY_NAME_TOMORROW: self._holiday_name(tomorrow),
            MetaType.VACATION_1: self._is_vacation_n(today, 1),
            MetaType.VACATION_2: self._is_vacation_n(today, 2),
            MetaType.VACATION_3: self._is_vacation_n(today, 3),
            MetaType.VACATION_4: self._is_vacation_n(today, 4),
            MetaType.VACATION_5: self._is_vacation_n(today, 5),
            MetaType.VACATION_6: self._is_vacation_n(today, 6),
        }

        from obs.core.event_bus import DataValueEvent

        for binding in list(self._bindings):
            try:
                cfg = ZeitschaltuhrBindingConfig(**binding.config)
                if cfg.timer_type == TimerType.META and cfg.meta_type != MetaType.NONE:
                    value = meta_values.get(cfg.meta_type)
                    if value is not None:
                        await self._bus.publish(
                            DataValueEvent(
                                datapoint_id=binding.datapoint_id,
                                value=value,
                                quality="good",
                                source_adapter=self.adapter_type,
                                binding_id=binding.id,
                            ),
                        )
            except Exception:
                logger.exception("Fehler beim Publishen von Meta-Binding %s", binding.id)

    # ------------------------------------------------------------------
    # Holiday helpers
    # ------------------------------------------------------------------

    def _build_holidays(self) -> dict[date, str]:
        year = datetime.now().year
        years = [year, year + 1]
        result: dict[date, str] = {}

        # Library holidays
        try:
            import holidays as hol_lib

            kwargs: dict[str, Any] = {"years": years}
            if self._cfg.holiday_subdivision:
                kwargs["subdiv"] = self._cfg.holiday_subdivision
            if self._cfg.holiday_language:
                kwargs["language"] = self._cfg.holiday_language
            try:
                hol_obj = hol_lib.country_holidays(self._cfg.holiday_country, **kwargs)
            except Exception:
                kwargs.pop("language", None)
                logger.debug(
                    "Sprache '%s' für Land '%s' nicht unterstützt — nutze Standardsprache",
                    self._cfg.holiday_language,
                    self._cfg.holiday_country,
                )
                hol_obj = hol_lib.country_holidays(self._cfg.holiday_country, **kwargs)
            result.update(dict(hol_obj.items()))
        except ImportError:
            logger.info("holidays-Bibliothek nicht installiert — Feiertagserkennung deaktiviert (pip install holidays)")
        except Exception as exc:
            logger.warning("Feiertagskalender konnte nicht geladen werden: %s", exc)

        # Custom holidays (merged on top — custom entries override library names for same date)
        for y in years:
            for entry in self._cfg.custom_holidays:
                try:
                    result.update(self._parse_custom_holiday_entry(entry, y))
                except Exception as exc:
                    logger.warning("Benutzerdefinierter Feiertag '%s' konnte nicht geparst werden: %s", entry, exc)

        return result

    def _parse_custom_holiday_entry(self, entry: str, year: int) -> dict[date, str]:
        """Parse one custom holiday expression and return {date: name} for the given year.

        Supported formats:
          MM-TT[:Name]              — fixed annual date (e.g. "12-26:Stephanstag")
          easter+N[:Name]           — N days after Easter Sunday (e.g. "easter+1:Ostermontag")
          easter-N[:Name]           — N days before Easter Sunday (e.g. "easter-47:Rosenmontag")
          advent+N[:Name]           — N days after 1st Advent (e.g. "advent+0:1. Advent")
          advent-N[:Name]           — N days before 1st Advent
          last_WT_MON[:Name]        — last weekday of month (e.g. "last_SO_NOV:Buß- und Bettag")
          N_WT_MON[:Name]           — n-th weekday of month (e.g. "2_SO_OKT:Erntedank")
        """
        parts = entry.split(":", 1)
        expr = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else "Benutzerdefinierter Feiertag"
        expr_up = expr.upper()

        # Fixed date: MM-TT (1-2 digit month/day)
        if re.fullmatch(r"\d{1,2}-\d{1,2}", expr):
            month, day = (int(x) for x in expr.split("-"))
            return {date(year, month, day): name}

        # Easter-relative: easter / easter+N / easter-N
        m = re.fullmatch(r"EASTER([+-]\d+)?", expr_up)
        if m:
            offset = int(m.group(1)) if m.group(1) else 0
            return {_easter_date(year) + timedelta(days=offset): name}

        # Advent-relative: advent / advent+N / advent-N
        m = re.fullmatch(r"ADVENT([+-]\d+)?", expr_up)
        if m:
            offset = int(m.group(1)) if m.group(1) else 0
            return {_advent1_date(year) + timedelta(days=offset): name}

        # Nth or last weekday of month: (LAST|N)_WT_MON
        m2 = re.fullmatch(r"(LAST|\d+)_([A-ZÄÖÜa-z]{2,3})_([A-ZÄÖÜa-z]{2,3})", expr_up)
        if m2:
            nth_str, wday_str, month_str = m2.groups()
            weekday = _WEEKDAY_ABBR.get(wday_str)
            month = _MONTH_ABBR.get(month_str)
            if weekday is None or month is None:
                logger.warning(
                    "Unbekanntes Wochentags- oder Monatskürzel in '%s' (Wochentag=%s, Monat=%s)",
                    entry,
                    wday_str,
                    month_str,
                )
                return {}
            if nth_str == "LAST":
                d = _last_weekday_of_month(year, month, weekday)
            else:
                d = _nth_weekday_of_month(year, month, weekday, int(nth_str))
            return {d: name} if d is not None else {}

        logger.warning("Unbekanntes Format für benutzerdefinierten Feiertag: '%s'", entry)
        return {}

    # ------------------------------------------------------------------
    # Date window helpers
    # ------------------------------------------------------------------

    def _parse_date_expression(self, expr: str, year: int) -> date | None:
        """Parse a date window expression for the given year.

        Formats:
          MM-TT               — fixed annual date (e.g. "05-01")
          easter[±N]          — relative to Easter Sunday
          advent[±N]          — relative to 1st Advent
          holiday:Name[±N]    — named holiday ± offset
        """
        if not expr or not (expr_s := expr.strip()):
            return None
        expr_up = expr_s.upper()

        # Fixed date MM-TT
        if re.fullmatch(r"\d{1,2}-\d{1,2}", expr_s):
            month, day = (int(x) for x in expr_s.split("-"))
            try:
                return date(year, month, day)
            except ValueError:
                return None

        # Easter-relative
        m = re.fullmatch(r"EASTER([+-]\d+)?", expr_up)
        if m:
            offset = int(m.group(1)) if m.group(1) else 0
            return _easter_date(year) + timedelta(days=offset)

        # Advent-relative
        m = re.fullmatch(r"ADVENT([+-]\d+)?", expr_up)
        if m:
            offset = int(m.group(1)) if m.group(1) else 0
            return _advent1_date(year) + timedelta(days=offset)

        # Named holiday: holiday:Name or holiday:Name±N
        if expr_up.startswith("HOLIDAY:"):
            remainder = expr_s[8:]
            off_m = re.search(r"([+-]\d+)$", remainder)
            if off_m:
                name = remainder[: off_m.start()].strip()
                offset = int(off_m.group(1))
            else:
                name = remainder.strip()
                offset = 0
            name_up = name.upper()
            # Fast path: self._hol covers current + next year
            for d, n in self._hol.items():
                if d.year == year and n.upper() == name_up:
                    return d + timedelta(days=offset)
            # Slow path: year not in self._hol (e.g. year-1 in cross-year check)
            try:
                for h in self.get_holidays_for_year(year):
                    if h["name"].upper() == name_up:
                        return date.fromisoformat(h["date"]) + timedelta(days=offset)
            except Exception:
                pass
            return None

        logger.debug("Unbekanntes Datum-Fenster-Ausdruck: '%s'", expr_s)
        return None

    def _in_date_window(self, from_expr: str, to_expr: str, today: date) -> bool:
        """Returns True if today falls within the [from, to] date window (inclusive)."""
        year = today.year
        d_from = self._parse_date_expression(from_expr, year)
        d_to = self._parse_date_expression(to_expr, year)
        if d_from is None or d_to is None:
            return False
        if d_from <= d_to:
            return d_from <= today <= d_to
        # Cross-year window (e.g. 1. Advent in Nov/Dec → Epiphany in Jan)
        d_to_next = self._parse_date_expression(to_expr, year + 1)
        if d_to_next is not None and d_from <= today <= d_to_next:
            return True
        d_from_prev = self._parse_date_expression(from_expr, year - 1)
        if d_from_prev is not None and d_from_prev <= today <= d_to:
            return True
        return False

    def get_holidays_for_year(self, year: int) -> list[dict]:
        """Returns all holidays for the given year as a date-sorted list of {date, name} dicts."""
        result: dict[date, str] = {}
        try:
            import holidays as hol_lib

            kwargs: dict[str, Any] = {"years": [year]}
            if self._cfg.holiday_subdivision:
                kwargs["subdiv"] = self._cfg.holiday_subdivision
            if self._cfg.holiday_language:
                kwargs["language"] = self._cfg.holiday_language
            try:
                hol_obj = hol_lib.country_holidays(self._cfg.holiday_country, **kwargs)
            except Exception:
                kwargs.pop("language", None)
                hol_obj = hol_lib.country_holidays(self._cfg.holiday_country, **kwargs)
            result.update(dict(hol_obj.items()))
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("Feiertagskalender konnte nicht geladen werden: %s", exc)

        for entry in self._cfg.custom_holidays:
            try:
                result.update(self._parse_custom_holiday_entry(entry, year))
            except Exception:
                pass

        return [{"date": d.isoformat(), "name": n} for d, n in sorted(result.items())]

    def _is_holiday(self, d: date) -> bool:
        return d in self._hol

    def _holiday_name(self, d: date) -> str:
        try:
            return self._hol.get(d, "")
        except Exception:
            return ""

    def _parse_vacation_period(self, n: int) -> tuple[date | None, date | None]:
        start_str: str = getattr(self._cfg, f"vacation_{n}_start", "")
        end_str: str = getattr(self._cfg, f"vacation_{n}_end", "")
        try:
            start = date.fromisoformat(start_str) if start_str else None
            end = date.fromisoformat(end_str) if end_str else None
            return start, end
        except ValueError:
            return None, None

    def _is_vacation_n(self, d: date, n: int) -> bool:
        start, end = self._parse_vacation_period(n)
        if start is None or end is None:
            return False
        return start <= d <= end

    def _is_vacation(self, d: date) -> bool:
        return any(self._is_vacation_n(d, n) for n in range(1, 7))

    # ------------------------------------------------------------------
    # Timezone resolution
    # ------------------------------------------------------------------

    async def _resolve_timezone(self) -> Any:
        import zoneinfo

        tz_name = self._cfg.timezone.strip()
        if not tz_name:
            try:
                from obs.db.database import get_db

                row = await get_db().fetchone("SELECT value FROM app_settings WHERE key='timezone'")
                if row:
                    tz_name = row["value"]
            except Exception:
                pass
        if not tz_name:
            tz_name = "UTC"
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            logger.warning("Zeitzone '%s' unbekannt — nutze UTC", tz_name)
            return UTC
