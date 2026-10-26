"""Version contract and channel rules of the update check (issue #1370).

OBS knows three kinds of version strings:

* **stable** — CalVer ``YYYY.M.P`` (``2026.10.0``),
* **rc** — a release candidate of one, ``YYYY.M.P-RC<n>`` (``2026.11.0-RC1``),
* **nightly** — ``nightly-YYYYMMDD``; an installed Docker nightly additionally
  carries the commit, ``nightly-YYYYMMDD-<sha>``.

Stable versions and RCs share one ordering (an RC sorts before its final
release). Nightlies are ordered by their build date only: the date digits are
never compared like CalVer components, so ``nightly-20261008`` is neither newer
nor older than ``2026.10.0`` by numbers. Where a stable/RC has to be compared
with a nightly (an installation that switched channel), the release's
publication date decides — see :func:`is_newer`.

Every other tag — drafts, ``-beta``/``-alpha`` tags of the early project,
``v``-prefixed tags — is outside the contract and ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

Channel = Literal["stable", "pre-release", "nightly"]
VersionKind = Literal["stable", "rc", "nightly"]

CHANNELS: tuple[Channel, ...] = ("stable", "pre-release", "nightly")
DEFAULT_CHANNEL: Channel = "stable"

_VERSIONED_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,3})(?:-RC(\d{1,3}))?$", re.IGNORECASE)
_NIGHTLY_RE = re.compile(r"^nightly-(\d{4})(\d{2})(\d{2})(-[0-9a-f]{7,40})?$")

# RC number given to a stable tag that GitHub marks as a pre-release (RCs are
# limited to three digits by the contract).
_AFTER_ALL_RCS = 1000

# Which version kinds each channel may offer as its newest version.
_CHANNEL_KINDS: dict[Channel, frozenset[VersionKind]] = {
    "stable": frozenset({"stable"}),
    "pre-release": frozenset({"stable", "rc"}),
    "nightly": frozenset({"nightly"}),
}


@dataclass(frozen=True)
class Version:
    """A parsed version string within the supported contract."""

    raw: str
    kind: VersionKind
    # stable/rc: (year, month, patch, is_final, rc) — an RC sorts before its final.
    # nightly:   the build date as (year, month, day); only compared with nightlies.
    key: tuple[int, ...]

    @property
    def nightly_date(self) -> date | None:
        if self.kind != "nightly":
            return None
        return date(*self.key)


def parse_version(raw: str | None, *, allow_commit_suffix: bool = True) -> Version | None:
    """Parse *raw* into a :class:`Version`, or ``None`` when it is outside the contract.

    ``allow_commit_suffix`` accepts the ``-<sha>`` suffix of an installed Docker
    nightly. Release tags never carry it, so the release list parses with it off.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    m = _VERSIONED_RE.match(text)
    if m:
        year, month, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not 1 <= month <= 12:
            return None
        if m.group(4) is None:
            return Version(text, "stable", (year, month, patch, 1, 0))
        return Version(text, "rc", (year, month, patch, 0, int(m.group(4))))
    m = _NIGHTLY_RE.match(text)
    if m:
        if m.group(4) and not allow_commit_suffix:
            return None
        try:
            built = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return Version(text, "nightly", (built.year, built.month, built.day))
    return None


def channel_for_version(raw: str | None) -> Channel:
    """The channel an installation of *raw* most likely follows (default: stable)."""
    version = parse_version(raw)
    if version is None or version.kind == "stable":
        return DEFAULT_CHANNEL
    if version.kind == "rc":
        return "pre-release"
    return "nightly"


def channel_allows(channel: Channel, kind: VersionKind) -> bool:
    return kind in _CHANNEL_KINDS[channel]


@dataclass(frozen=True)
class Release:
    """One published GitHub release that falls inside the version contract."""

    version: Version
    published_at: datetime | None
    url: str
    body: str
    name: str

    @property
    def tag(self) -> str:
        return self.version.raw


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def classify_releases(payload: Any) -> list[Release]:
    """Turn the GitHub ``/releases`` payload into the releases OBS understands.

    Drafts, malformed entries and tags outside the contract are dropped. A
    release whose tag reads as a stable version but which GitHub marks as a
    pre-release is not offered as stable either — it is treated as an RC of that
    version, so only the pre-release channel can see it.
    """
    if not isinstance(payload, list):
        raise TypeError("release list must be a JSON array")
    releases: list[Release] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft") is not False:
            continue
        version = parse_version(item.get("tag_name"), allow_commit_suffix=False)
        if version is None:
            continue
        if version.kind == "stable" and item.get("prerelease") is True:
            # Still the final build of that version: after all of its RCs.
            version = Version(version.raw, "rc", version.key[:3] + (0, _AFTER_ALL_RCS))
        body = item.get("body")
        url = item.get("html_url")
        name = item.get("name")
        releases.append(
            Release(
                version=version,
                published_at=_parse_timestamp(item.get("published_at")),
                url=url if isinstance(url, str) and url.startswith("https://") else "",
                body=body if isinstance(body, str) else "",
                name=name if isinstance(name, str) and name else version.raw,
            )
        )
    return releases


def _published_date(installed: Version, releases: list[Release]) -> date | None:
    # By tag, not by key: a stable tag GitHub marks as a pre-release has an RC
    # key. Case-insensitive like parse_version: "-rc1" installs from "-RC1".
    tag = installed.raw.casefold()
    for release in releases:
        if release.version.raw.casefold() == tag and release.published_at is not None:
            return release.published_at.astimezone(UTC).date()
    return None


def is_newer(candidate: Release, installed: Version, releases: list[Release]) -> bool | None:
    """Whether *candidate* is newer than the *installed* version.

    ``None`` means the two cannot be ordered reliably. Within one family the
    version keys decide. Across families (a nightly against a stable/RC) only
    calendar days are compared, and only a strictly later day counts as newer:
    a nightly and a release published the same day cannot be told apart.
    """
    cand = candidate.version
    if (cand.kind == "nightly") == (installed.kind == "nightly"):
        return cand.key > installed.key
    if installed.kind == "nightly":
        if candidate.published_at is None:
            return None
        return candidate.published_at.astimezone(UTC).date() > installed.nightly_date
    installed_day = _published_date(installed, releases)
    if installed_day is None:
        return None
    return cand.nightly_date > installed_day


def _sort_key(release: Release) -> tuple:
    published = release.published_at or datetime.min.replace(tzinfo=UTC)
    return (release.version.key, published)


@dataclass(frozen=True)
class ChannelEvaluation:
    latest: Release | None
    # True/False only from a successful, channel-correct comparison; None when
    # the installed version cannot be ordered against the channel's releases.
    update_available: bool | None
    # Releases newer than the installed version up to and including ``latest``,
    # oldest first. Empty unless ``update_available`` is True.
    pending: list[Release]


def evaluate_channel(channel: Channel, installed_raw: str, releases: list[Release]) -> ChannelEvaluation:
    """Find the newest release of *channel* and what lies between it and *installed_raw*."""
    eligible = [r for r in releases if channel_allows(channel, r.version.kind)]
    if not eligible:
        return ChannelEvaluation(latest=None, update_available=None, pending=[])
    eligible.sort(key=_sort_key)
    latest = eligible[-1]
    installed = parse_version(installed_raw)
    if installed is None:
        return ChannelEvaluation(latest=latest, update_available=None, pending=[])
    newer = is_newer(latest, installed, releases)
    if not newer:
        return ChannelEvaluation(latest=latest, update_available=newer, pending=[])
    pending = [r for r in eligible if is_newer(r, installed, releases)]
    return ChannelEvaluation(latest=latest, update_available=True, pending=pending)
