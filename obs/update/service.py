"""Channel-aware update check (issue #1370).

The service asks GitHub for the project's releases, classifies them with
:mod:`obs.update.versions` and keeps the result in memory, in ``app_settings``
and in the read-only system DataPoints of :mod:`obs.core.system_datapoints`.

Schedule: the first check runs a random 0–719 minutes after start-up, so
installations spread their requests across the whole twelve-hour window; after
that it runs every twelve hours. A failed check does not retry early — the next
attempt is the next regular one (or a manual check from the Admin GUI).

A failure never touches the last successful result: the latest version, the
release notes and ``update_available`` stay as they were, and the status only
gains an error code and a new ``last_check_at``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

import obs
from obs.core.system_datapoints import (
    CURRENT_VERSION,
    LAST_CHECK,
    LATEST_VERSION,
    SYSTEM_SOURCE_ADAPTER,
    UPDATE_AVAILABLE,
    UPDATE_CHANNEL,
    SystemDataPointDef,
)
from obs.update.versions import CHANNELS, Channel, Release, channel_for_version, classify_releases, evaluate_channel, parse_version

logger = logging.getLogger(__name__)

CHANNEL_SETTING_KEY = "update.channel"
STATE_SETTING_KEY = "update.check_state"
# Present while the channel is the one OBS derived from the installed version
# (value: that version); an admin's choice removes it and is then kept as is.
CHANNEL_DERIVED_KEY = "update.channel_derived_from"

CHECK_INTERVAL_SECONDS = 12 * 60 * 60
# The first check after start-up waits a whole number of minutes in [0, 719].
INITIAL_DELAY_MAX_MINUTES = 719
# A manual check within this many seconds of the previous attempt returns the
# current status instead of asking GitHub again.
MANUAL_CHECK_COOLDOWN_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15.0
RELEASES_PER_PAGE = 100
# Upper bound on followed pages, against a Link header that never ends.
MAX_RELEASE_PAGES = 10


def resolve_installed_version(package_version: str | None = None, install_root: Path | None = None) -> str:
    """The version this installation runs.

    ``obs/version`` (``obs.__version__``) is stamped at build time. An LXC
    installation also has ``<install root>/version``, which ``obs-update`` writes
    with the exact release tag it installed, and which a fresh LXC template
    carries from its build. Bundles built before #1370 stamp a nightly with the
    base version from RELEASENOTES.md, so for them that file is the only place
    that says ``nightly-YYYYMMDD``. It wins when it holds a version inside the
    contract.
    """
    package_version = (obs.__version__ if package_version is None else package_version).strip()
    root = Path(obs.__file__).resolve().parent.parent if install_root is None else install_root
    try:
        installed = (root / "version").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return package_version
    # The migration patch tag (2026.99.0) is outside the contract too: month 99.
    if parse_version(installed) is not None:
        return installed
    return package_version


class UpdateCheckError(Exception):
    """A check attempt failed; ``code`` is the stable key the GUI translates."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass
class FetchResult:
    not_modified: bool
    etag: str | None
    payload: Any = None


class GitHubReleaseSource:
    """Reads ``GET /repos/{repository}/releases`` with conditional requests."""

    def __init__(
        self, api_url: str, repository: str, *, timeout: float = REQUEST_TIMEOUT_SECONDS, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = f"{api_url.rstrip('/')}/repos/{repository}/releases"
        self._timeout = timeout
        self._transport = transport

    async def fetch(self, etag: str | None) -> FetchResult:
        """All pages of the release list; the ETag belongs to the first one.

        Every new release lands on the first page, so an unchanged first page
        (304) means an unchanged list. The later pages matter all the same: the
        installed release, whose publication date orders it against nightlies,
        falls off the first page once a hundred newer releases exist.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"openbridgeserver/{obs.__version__}",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport, follow_redirects=True) as client:
                first_headers = {**headers, "If-None-Match": etag} if etag else headers
                response = await client.get(self._url, params={"per_page": RELEASES_PER_PAGE}, headers=first_headers)
                # A 304 only means something when this request carried a cached ETag.
                if response.status_code == 304 and etag:
                    return FetchResult(not_modified=True, etag=response.headers.get("etag") or etag)
                first_etag = response.headers.get("etag")
                payload = _json_page(response)
                pages = 1
                while isinstance(payload, list) and (next_url := self._next_page(response)) and pages < MAX_RELEASE_PAGES:
                    response = await client.get(next_url, headers=headers)
                    page = _json_page(response)
                    if not isinstance(page, list):
                        raise UpdateCheckError("invalid_response", "release page is not a JSON array")
                    payload.extend(page)
                    pages += 1
        except httpx.HTTPError as exc:
            raise UpdateCheckError("network", type(exc).__name__) from exc
        return FetchResult(not_modified=False, etag=first_etag, payload=payload)

    def _next_page(self, response: httpx.Response) -> str | None:
        """The ``Link: rel="next"`` URL, followed only on the configured API host."""
        url = response.links.get("next", {}).get("url")
        if not url:
            return None
        target, own = httpx.URL(url), httpx.URL(self._url)
        return url if (target.scheme, target.host, target.port) == (own.scheme, own.host, own.port) else None


def _json_page(response: httpx.Response) -> Any:
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        raise UpdateCheckError("rate_limited", str(response.status_code))
    if response.status_code != 200:
        raise UpdateCheckError("http_status", str(response.status_code))
    try:
        return response.json()
    except ValueError as exc:
        raise UpdateCheckError("invalid_response", "body is not JSON") from exc


@dataclass
class ReleaseNote:
    version: str
    name: str
    published_at: str | None
    url: str
    body: str

    @classmethod
    def from_release(cls, release: Release) -> ReleaseNote:
        return cls(
            version=release.tag,
            name=release.name,
            published_at=release.published_at.isoformat() if release.published_at else None,
            url=release.url,
            body=release.body,
        )


@dataclass
class UpdateState:
    channel: Channel
    installed_version: str
    latest_version: str | None = None
    latest_url: str | None = None
    update_available: bool | None = None
    release_notes: list[ReleaseNote] = field(default_factory=list)
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_detail: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "channel": self.channel,
                "installed_version": self.installed_version,
                "latest_version": self.latest_version,
                "latest_url": self.latest_url,
                "update_available": self.update_available,
                "release_notes": [note.__dict__ for note in self.release_notes],
                "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
                "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
                "last_error": self.last_error,
                "last_error_detail": self.last_error_detail,
            }
        )


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # A naive value (hand-edited state) would break arithmetic with aware times.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _restore_state(raw: str | None, channel: Channel, installed_version: str) -> UpdateState | None:
    """The persisted state, when it was computed for this channel and version.

    After an update was installed or the channel changed, the stored result
    answers a different question, so it is discarded rather than shown.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("channel") != channel or data.get("installed_version") != installed_version:
            return None
        available = data.get("update_available")
        notes = [
            ReleaseNote(
                version=str(n["version"]),
                name=str(n.get("name") or n["version"]),
                published_at=n.get("published_at") if isinstance(n.get("published_at"), str) else None,
                url=str(n.get("url") or ""),
                body=str(n.get("body") or ""),
            )
            for n in data.get("release_notes") or []
        ]
        return UpdateState(
            channel=channel,
            installed_version=installed_version,
            latest_version=data.get("latest_version") if isinstance(data.get("latest_version"), str) else None,
            latest_url=data.get("latest_url") if isinstance(data.get("latest_url"), str) else None,
            update_available=available if isinstance(available, bool) else None,
            release_notes=notes,
            last_check_at=_parse_dt(data.get("last_check_at")),
            last_success_at=_parse_dt(data.get("last_success_at")),
            last_error=data.get("last_error") if isinstance(data.get("last_error"), str) else None,
            last_error_detail=data.get("last_error_detail") if isinstance(data.get("last_error_detail"), str) else None,
        )
    except (ValueError, TypeError, KeyError):
        logger.warning("Update check: ignoring unreadable persisted state")
        return None


async def _write_setting(db: Any, key: str, value: str) -> None:
    await db.execute_and_commit(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


class UpdateCheckService:
    def __init__(
        self,
        db: Any,
        event_bus: Any,
        *,
        source: GitHubReleaseSource | None,
        installed_version: str,
        rng: random.Random | None = None,
        clock: Any = None,
    ) -> None:
        self._db = db
        self._bus = event_bus
        self._source = source
        self._installed_version = installed_version
        self._rng = rng or random.Random()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._state = UpdateState(channel=channel_for_version(installed_version), installed_version=installed_version)
        # Last successful release list; channel independent, so a channel
        # change can be answered from it without asking GitHub again.
        self._releases: list[Release] | None = None
        self._releases_fetched_at: datetime | None = None
        # The last check attempt of this process; the manual cool-down counts from it.
        self._last_attempt_at: datetime | None = None
        self._etag: str | None = None
        # Guards the state; never held across a GitHub request, so a channel
        # change or a database restore does not wait for a slow fetch.
        self._lock = asyncio.Lock()
        # Serialises check attempts (fetch included) among themselves.
        self._check_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._check_running = False
        # Last (value, quality) sent per system DataPoint: unchanged values are
        # not sent again, so a MESSAGE binding or Logic graph on e.g. "OBS Update
        # Available" does not fire on every twelve-hourly check.
        self._published: dict[Any, tuple[Any, str]] = {}
        self._channel_derived = False
        self.next_check_at: datetime | None = None

    # ------------------------------------------------------------------
    # Read-only view
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._source is not None

    @property
    def state(self) -> UpdateState:
        return self._state

    @property
    def checking(self) -> bool:
        return self._check_running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """Read channel and last result from the database and publish them.

        Runs once at start-up; after a database restore keep_after_restore()
        writes this installation's channel and status back instead.
        """
        # Under the lock: a check taking its result over must not split it
        # between the state it started with and the one restored from the database.
        async with self._lock:
            row = await self._db.fetchone("SELECT value FROM app_settings WHERE key = ?", (CHANNEL_SETTING_KEY,))
            channel = row["value"] if row and row["value"] in CHANNELS else None
            self._channel_derived = await self._db.fetchone("SELECT value FROM app_settings WHERE key = ?", (CHANNEL_DERIVED_KEY,)) is not None
            derived_channel = channel_for_version(self._installed_version)
            # A derived channel follows the installation: after obs-update moved it
            # from a nightly to a stable (or back) it is derived anew. An admin's
            # choice (no marker) stays.
            if channel is None or (self._channel_derived and channel != derived_channel):
                channel = derived_channel
                self._channel_derived = True
                await _write_setting(self._db, CHANNEL_SETTING_KEY, channel)
                await self._write_derived_marker()
                logger.info("Update check: update channel '%s' derived from version %s", channel, self._installed_version)
            state_row = await self._db.fetchone("SELECT value FROM app_settings WHERE key = ?", (STATE_SETTING_KEY,))
            # With the check disabled nothing would ever refresh a restored result,
            # so it must not be presented as current.
            raw_state = state_row["value"] if state_row and self._source is not None else None
            restored = _restore_state(raw_state, channel, self._installed_version)
            self._state = restored or UpdateState(channel=channel, installed_version=self._installed_version)
            # The registry starts every DataPoint empty: send all values once.
            # Restored, not changed: a MESSAGE binding must not notify again.
            await self.publish_datapoints(force=True, initialization=True)

    async def keep_after_restore(self) -> None:
        """Write this installation's channel and status back after a database restore.

        Like config import and factory reset, a restored backup must not carry
        the update channel or a cached result of another time or host over: both
        belong to the running installation. The restore also gave the system
        DataPoints fresh, empty values, so they are published again.
        """
        async with self._lock:
            await _write_setting(self._db, CHANNEL_SETTING_KEY, self._state.channel)
            await self._write_derived_marker()
            await _write_setting(self._db, STATE_SETTING_KEY, self._state.to_json())
            await self.publish_datapoints(force=True, initialization=True)

    async def _write_derived_marker(self) -> None:
        if self._channel_derived:
            await _write_setting(self._db, CHANNEL_DERIVED_KEY, self._installed_version)
        else:
            await self._db.execute_and_commit("DELETE FROM app_settings WHERE key = ?", (CHANNEL_DERIVED_KEY,))

    def start(self) -> None:
        if self._source is None:
            logger.info("Update check disabled by configuration (update_check.enabled = false)")
            return
        self._task = asyncio.create_task(self._run(), name="update-check")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def initial_delay_seconds(self) -> int:
        return self._rng.randint(0, INITIAL_DELAY_MAX_MINUTES) * 60

    async def _run(self) -> None:
        delay = self.initial_delay_seconds()
        self.next_check_at = self._clock() + timedelta(seconds=delay)
        logger.info("Update check: first check in %d minutes", delay // 60)
        await asyncio.sleep(delay)
        while True:
            await self.check()
            self.next_check_at = self._clock() + timedelta(seconds=CHECK_INTERVAL_SECONDS)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def check(self) -> UpdateState:
        """Run one check attempt. Never raises for network or data errors."""
        async with self._check_lock:
            return await self._check_serialized()

    async def _check_serialized(self) -> UpdateState:
        """The check itself; the caller holds ``self._check_lock``.

        The GitHub request runs outside ``self._lock``; only taking its result
        over and persisting it is done under it, against the state current then
        (e.g. a channel chosen while the request was in flight).
        """
        self._check_running = True
        try:
            now = self._clock()
            self._last_attempt_at = now
            releases: list[Release] | None = None
            failure: UpdateCheckError | None = None
            try:
                releases, etag = await self._fetch_releases()
            except asyncio.CancelledError:
                raise
            except UpdateCheckError as exc:
                failure = exc
            except Exception:
                logger.exception("Update check: unexpected failure")
                failure = UpdateCheckError("internal")
            async with self._lock:
                self._state.last_check_at = now
                if failure is not None:
                    self._record_failure(failure)
                else:
                    self._releases = releases
                    self._releases_fetched_at = now
                    self._etag = etag
                    self._apply_evaluation(now)
                await self._persist_and_publish()
                return self._state
        finally:
            self._check_running = False

    async def check_now(self) -> tuple[UpdateState, bool]:
        """Manual check from the Admin GUI.

        Does not ask GitHub again within the cool-down after the previous
        attempt. The cool-down is evaluated only once the check lock is held:
        callers queued behind a running check would otherwise all pass it and
        each send a request; this way the first one checks and the others take
        its result. Returns the state and whether this call ran a check.
        """
        async with self._check_lock:
            # This process's own attempt, not the restored last_check_at: after a
            # restart nothing is cached, and a check the previous process ran just
            # before must not leave e.g. a channel switch without an answer.
            last = self._last_attempt_at
            # A last check "in the future" (clock set back since) must not block checks.
            if last is not None and 0 <= (self._clock() - last).total_seconds() < MANUAL_CHECK_COOLDOWN_SECONDS:
                return self._state, False
            return await self._check_serialized(), True

    async def _fetch_releases(self) -> tuple[list[Release] | None, str | None]:
        """The classified release list and its ETag; raises UpdateCheckError."""
        # Only checks touch the cache, and they are serialised by _check_lock.
        result = await self._source.fetch(self._etag if self._releases is not None else None)
        if result.not_modified:
            return self._releases, result.etag
        try:
            return classify_releases(result.payload), result.etag
        except TypeError as exc:
            raise UpdateCheckError("invalid_response", str(exc)) from exc

    def _record_failure(self, exc: UpdateCheckError) -> None:
        self._state.last_error = exc.code
        self._state.last_error_detail = exc.detail or None
        logger.warning("Update check failed (%s): %s", exc.code, exc.detail or "-")

    def _apply_evaluation(self, success_at: datetime) -> None:
        evaluation = evaluate_channel(self._state.channel, self._installed_version, self._releases)
        self._state.latest_version = evaluation.latest.tag if evaluation.latest else None
        self._state.latest_url = evaluation.latest.url if evaluation.latest else None
        self._state.update_available = evaluation.update_available
        self._state.release_notes = [ReleaseNote.from_release(r) for r in evaluation.pending]
        self._state.last_success_at = success_at
        self._state.last_error = None
        self._state.last_error_detail = None
        if evaluation.update_available:
            logger.info(
                "Update check: %s is available in channel '%s' (installed: %s)", evaluation.latest.tag, self._state.channel, self._installed_version
            )

    async def set_channel(self, channel: Channel) -> tuple[UpdateState, bool]:
        """Switch the channel; returns the state and whether GitHub was asked."""
        if channel not in CHANNELS:
            raise ValueError(f"unknown update channel {channel!r}")
        async with self._lock:
            await _write_setting(self._db, CHANNEL_SETTING_KEY, channel)
            self._channel_derived = False
            await self._write_derived_marker()
            changed = channel != self._state.channel
            if changed:
                previous = self._state
                self._state = UpdateState(channel=channel, installed_version=self._installed_version, last_check_at=previous.last_check_at)
                if self._releases_fetched_at is not None:
                    self._apply_evaluation(self._releases_fetched_at)
                # The last attempt's outcome stays visible: with a cached list that
                # predates a failure the result may be outdated, and without one the
                # failure is the only thing known about the new channel yet.
                self._state.last_error = previous.last_error
                self._state.last_error_detail = previous.last_error_detail
            await self._persist_and_publish()
        if changed and self._state.last_success_at is None and self._source is not None:
            # No release list answers the new channel yet: ask GitHub right away
            # (within the manual cool-down), so the response already carries the
            # status of the chosen channel.
            return await self.check_now()
        return self._state, False

    # ------------------------------------------------------------------
    # Persistence and DataPoints
    # ------------------------------------------------------------------

    async def _persist_and_publish(self) -> None:
        try:
            await _write_setting(self._db, STATE_SETTING_KEY, self._state.to_json())
        except Exception:
            logger.exception("Update check: could not persist status")
        await self.publish_datapoints()

    async def publish_datapoints(self, *, force: bool = False, initialization: bool = False) -> None:
        """Send the system DataPoints; unchanged values only with ``force``.

        ``initialization`` marks values restored rather than changed (start-up,
        database restore): they still reach the registry and DEST bindings, but
        notification subscribers such as MESSAGE ignore them, so an unchanged
        "update available" does not notify again after every restart.
        """
        state = self._state
        known = state.last_success_at is not None
        values = (
            (UPDATE_CHANNEL, state.channel, "good"),
            (CURRENT_VERSION, state.installed_version, "good"),
            (LATEST_VERSION, state.latest_version, "good" if known and state.latest_version else "uncertain"),
            (UPDATE_AVAILABLE, state.update_available, "good" if known and state.update_available is not None else "uncertain"),
            (LAST_CHECK, state.last_check_at, "good" if state.last_check_at else "uncertain"),
        )
        for definition, value, quality in values:
            await self._publish(definition, value, quality, force, initialization)

    async def _publish(self, definition: SystemDataPointDef, value: Any, quality: str, force: bool, initialization: bool) -> None:
        from obs.core.event_bus import DataValueEvent

        sent = (value if quality == "good" else None, quality)
        if not force and self._published.get(definition.id) == sent:
            return
        try:
            await self._bus.publish(
                DataValueEvent(
                    datapoint_id=definition.id,
                    value=sent[0],
                    quality=quality,
                    source_adapter=SYSTEM_SOURCE_ADAPTER,
                    initialization=initialization,
                )
            )
            self._published[definition.id] = sent
        except Exception:
            logger.exception("Update check: could not publish %s", definition.name)


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_service: UpdateCheckService | None = None


def get_update_service() -> UpdateCheckService:
    if _service is None:
        raise RuntimeError("UpdateCheckService not initialized")
    return _service


def get_optional_update_service() -> UpdateCheckService | None:
    return _service


def reset_update_service() -> None:
    """For testing only."""
    global _service
    _service = None


async def init_update_service(db: Any, event_bus: Any, settings: Any) -> UpdateCheckService:
    global _service
    source = GitHubReleaseSource(settings.api_url, settings.repository) if settings.enabled else None
    _service = UpdateCheckService(db, event_bus, source=source, installed_version=resolve_installed_version())
    await _service.load()
    _service.start()
    return _service
