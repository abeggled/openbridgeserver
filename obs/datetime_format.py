"""Application-wide date/time formatting helpers."""

from __future__ import annotations

from datetime import datetime

DEFAULT_DATE_FORMAT = "dd.MM.yyyy"
DEFAULT_TIME_FORMAT = "HH:mm:ss"
DEFAULT_CUSTOM_FORMAT = "EEEE, MMMM d, yyyy HH:mm:ss"

_WEEKDAYS_EN = (
    ("Monday", "Mon", "Mo"),
    ("Tuesday", "Tue", "Tu"),
    ("Wednesday", "Wed", "We"),
    ("Thursday", "Thu", "Th"),
    ("Friday", "Fri", "Fr"),
    ("Saturday", "Sat", "Sa"),
    ("Sunday", "Sun", "Su"),
)
_MONTHS_EN = (
    ("January", "Jan"),
    ("February", "Feb"),
    ("March", "Mar"),
    ("April", "Apr"),
    ("May", "May"),
    ("June", "Jun"),
    ("July", "Jul"),
    ("August", "Aug"),
    ("September", "Sep"),
    ("October", "Oct"),
    ("November", "Nov"),
    ("December", "Dec"),
)
_WEEKDAYS_DE = (
    ("Montag", "Mo.", "Mo"),
    ("Dienstag", "Di.", "Di"),
    ("Mittwoch", "Mi.", "Mi"),
    ("Donnerstag", "Do.", "Do"),
    ("Freitag", "Fr.", "Fr"),
    ("Samstag", "Sa.", "Sa"),
    ("Sonntag", "So.", "So"),
)
_MONTHS_DE = (
    ("Januar", "Jan."),
    ("Februar", "Feb."),
    ("März", "März"),
    ("April", "Apr."),
    ("Mai", "Mai"),
    ("Juni", "Juni"),
    ("Juli", "Juli"),
    ("August", "Aug."),
    ("September", "Sept."),
    ("Oktober", "Okt."),
    ("November", "Nov."),
    ("Dezember", "Dez."),
)
_WEEKDAYS_ES = (
    ("lunes", "lun", "lu"),
    ("martes", "mar", "ma"),
    ("miércoles", "mié", "mi"),
    ("jueves", "jue", "ju"),
    ("viernes", "vie", "vi"),
    ("sábado", "sáb", "sá"),
    ("domingo", "dom", "do"),
)
_MONTHS_ES = (
    ("enero", "ene"),
    ("febrero", "feb"),
    ("marzo", "mar"),
    ("abril", "abr"),
    ("mayo", "may"),
    ("junio", "jun"),
    ("julio", "jul"),
    ("agosto", "ago"),
    ("septiembre", "sept"),
    ("octubre", "oct"),
    ("noviembre", "nov"),
    ("diciembre", "dic"),
)
_WEEKDAYS_FR = (
    ("lundi", "lun.", "lu"),
    ("mardi", "mar.", "ma"),
    ("mercredi", "mer.", "me"),
    ("jeudi", "jeu.", "je"),
    ("vendredi", "ven.", "ve"),
    ("samedi", "sam.", "sa"),
    ("dimanche", "dim.", "di"),
)
_MONTHS_FR = (
    ("janvier", "janv."),
    ("février", "févr."),
    ("mars", "mars"),
    ("avril", "avr."),
    ("mai", "mai"),
    ("juin", "juin"),
    ("juillet", "juil."),
    ("août", "août"),
    ("septembre", "sept."),
    ("octobre", "oct."),
    ("novembre", "nov."),
    ("décembre", "déc."),
)
_WEEKDAYS_IT = (
    ("lunedì", "lun", "lu"),
    ("martedì", "mar", "ma"),
    ("mercoledì", "mer", "me"),
    ("giovedì", "gio", "gi"),
    ("venerdì", "ven", "ve"),
    ("sabato", "sab", "sa"),
    ("domenica", "dom", "do"),
)
_MONTHS_IT = (
    ("gennaio", "gen"),
    ("febbraio", "feb"),
    ("marzo", "mar"),
    ("aprile", "apr"),
    ("maggio", "mag"),
    ("giugno", "giu"),
    ("luglio", "lug"),
    ("agosto", "ago"),
    ("settembre", "set"),
    ("ottobre", "ott"),
    ("novembre", "nov"),
    ("dicembre", "dic"),
)
_LOCALIZED_NAMES = {
    "de": (_WEEKDAYS_DE, _MONTHS_DE),
    "gsw": (_WEEKDAYS_DE, _MONTHS_DE),
    "en": (_WEEKDAYS_EN, _MONTHS_EN),
    "es": (_WEEKDAYS_ES, _MONTHS_ES),
    "fr": (_WEEKDAYS_FR, _MONTHS_FR),
    "it": (_WEEKDAYS_IT, _MONTHS_IT),
}
_TOKENS = ("EEEE", "MMMM", "EEE", "MMM", "EE", "yyyy", "HH", "mm", "ss", "dd", "MM", "yy", "H", "m", "s", "d", "M")


def format_datetime(value: datetime, pattern: str, language: str = "de") -> str:
    """Format *value* with OBS tokens, leaving other characters untouched."""
    weekdays, months = _LOCALIZED_NAMES.get(language, _LOCALIZED_NAMES["en"])
    weekday = weekdays[value.weekday()]
    month = months[value.month - 1]
    replacements = {
        "HH": f"{value.hour:02d}",
        "H": str(value.hour),
        "mm": f"{value.minute:02d}",
        "m": str(value.minute),
        "ss": f"{value.second:02d}",
        "s": str(value.second),
        "dd": f"{value.day:02d}",
        "d": str(value.day),
        "EEEE": weekday[0],
        "EEE": weekday[1],
        "EE": weekday[2],
        "MMMM": month[0],
        "MMM": month[1],
        "MM": f"{value.month:02d}",
        "M": str(value.month),
        "yyyy": f"{value.year:04d}",
        "yy": f"{value.year % 100:02d}",
    }
    result: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern[index].isalpha():
            end = index + 1
            while end < len(pattern) and pattern[end].isalpha():
                end += 1
            word = pattern[index:end]
            word_result: list[str] = []
            word_index = 0
            while word_index < len(word):
                token = next((candidate for candidate in _TOKENS if word.startswith(candidate, word_index)), None)
                if token is None:
                    word_result = [word]
                    break
                word_result.append(replacements[token])
                word_index += len(token)
            result.extend(word_result)
            index = end
        else:
            result.append(pattern[index])
            index += 1
    return "".join(result)
