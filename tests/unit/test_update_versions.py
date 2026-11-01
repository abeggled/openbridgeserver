"""Version contract and channel rules of the update check (issue #1370)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from obs.update.versions import (
    Release,
    channel_allows,
    channel_for_version,
    classify_releases,
    evaluate_channel,
    is_newer,
    parse_version,
)


def _gh(tag, *, prerelease=False, draft=False, published="2026-10-08T19:40:34Z", body="notes", name=None, url=None):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "published_at": published,
        "body": body,
        "name": name if name is not None else f"Release {tag}",
        "html_url": url if url is not None else f"https://github.com/o/r/releases/tag/{tag}",
    }


# Mirrors the real release list shape: nightlies interleaved with stables and RCs,
# plus tags outside the contract from the early project.
RELEASES = classify_releases(
    [
        _gh("nightly-20261017", prerelease=True, published="2026-10-17T07:04:23Z"),
        _gh("nightly-20261009", prerelease=True, published="2026-10-09T09:17:50Z"),
        _gh("2026.11.0-RC1", prerelease=True, published="2026-10-12T10:00:00Z"),
        _gh("2026.10.0", published="2026-10-08T19:40:34Z"),
        _gh("nightly-20261008", prerelease=True, published="2026-10-08T09:10:57Z"),
        _gh("2026.9.1", published="2026-09-08T20:22:13Z"),
        _gh("2026.9.0", published="2026-09-07T19:57:15Z"),
        _gh("2026.8.0", published="2026-08-10T18:38:42Z"),
        _gh("2026.8.0-RC1", prerelease=True, published="2026-08-05T14:33:24Z"),
        _gh("2026.4.4-beta", prerelease=True),
        _gh("v0.1.1-alpha", prerelease=True),
        _gh("2026.12.0", draft=True),
    ]
)


# ---------------------------------------------------------------------------
# parse_version / channel_for_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "key"),
    [
        ("2026.10.0", "stable", (2026, 10, 0, 1, 0)),
        (" 2026.9.1\n", "stable", (2026, 9, 1, 1, 0)),
        ("2026.11.0-RC1", "rc", (2026, 11, 0, 0, 1)),
        ("2026.11.0-rc12", "rc", (2026, 11, 0, 0, 12)),
        ("nightly-20261017", "nightly", (2026, 10, 17)),
        ("nightly-20261017-7865b4d", "nightly", (2026, 10, 17)),
    ],
)
def test_parse_version_accepts_the_contract(raw, kind, key):
    version = parse_version(raw)
    assert version is not None
    assert (version.kind, version.key) == (kind, key)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        20261010,
        "",
        "dev-version",
        "2026.4.4-beta",
        "v0.1.1-alpha",
        "2026.13.0",
        "2026.0.1",
        "2026.10",
        "nightly-20261340",
        "nightly-2026101",
        "2026.10.0-RC",
    ],
)
def test_parse_version_rejects_everything_else(raw):
    assert parse_version(raw) is None


def test_release_tags_never_carry_a_commit_suffix():
    assert parse_version("nightly-20261017-7865b4d", allow_commit_suffix=False) is None
    assert parse_version("nightly-20261017", allow_commit_suffix=False) is not None


def test_nightly_date_only_for_nightlies():
    assert parse_version("nightly-20261017").nightly_date == date(2026, 10, 17)
    assert parse_version("2026.10.0").nightly_date is None


@pytest.mark.parametrize(
    ("raw", "channel"),
    [
        ("2026.10.0", "stable"),
        ("2026.11.0-RC1", "pre-release"),
        ("nightly-20261017", "nightly"),
        ("nightly-20261017-7865b4d", "nightly"),
        ("dev-version", "stable"),
        (None, "stable"),
    ],
)
def test_initial_channel_follows_the_installed_version(raw, channel):
    assert channel_for_version(raw) == channel


def test_channel_kind_table():
    assert channel_allows("stable", "stable")
    assert not channel_allows("stable", "rc")
    assert not channel_allows("stable", "nightly")
    assert channel_allows("pre-release", "stable")
    assert channel_allows("pre-release", "rc")
    assert not channel_allows("pre-release", "nightly")
    assert channel_allows("nightly", "nightly")
    assert not channel_allows("nightly", "stable")
    assert not channel_allows("nightly", "rc")


# ---------------------------------------------------------------------------
# classify_releases
# ---------------------------------------------------------------------------


def test_classify_drops_drafts_and_unknown_tags():
    tags = [r.tag for r in RELEASES]
    assert "2026.12.0" not in tags
    assert "2026.4.4-beta" not in tags
    assert "v0.1.1-alpha" not in tags
    assert len(tags) == 9


def test_classify_rejects_a_non_list_payload():
    with pytest.raises(TypeError):
        classify_releases({"message": "Not Found"})


def test_classify_skips_malformed_entries_and_normalises_fields():
    releases = classify_releases(
        [
            "not a dict",
            {"tag_name": "2026.10.0"},  # no draft flag → not trusted
            {"tag_name": "2026.10.0", "draft": False, "body": None, "name": "", "html_url": "javascript:alert(1)", "published_at": "garbage"},
            {"tag_name": "2026.9.0", "draft": False, "published_at": "2026-09-07T19:57:15"},
            {"tag_name": "nightly-20261017-7865b4d", "draft": False},
            {"tag_name": "2026.8.0", "draft": False},
        ]
    )
    assert [r.tag for r in releases] == ["2026.10.0", "2026.9.0", "2026.8.0"]
    first, second, third = releases
    assert third.published_at is None
    assert (first.body, first.name, first.url, first.published_at) == ("", "2026.10.0", "", None)
    assert second.published_at == datetime(2026, 9, 7, 19, 57, 15, tzinfo=UTC)


def test_classify_treats_a_stable_tag_marked_prerelease_as_rc():
    (release,) = classify_releases([_gh("2026.11.0", prerelease=True)])
    assert release.version.kind == "rc"
    assert not channel_allows("stable", release.version.kind)


def test_a_stable_tag_marked_prerelease_sorts_after_its_rcs():
    releases = classify_releases([_gh("2026.11.0-RC2", prerelease=True), _gh("2026.11.0", prerelease=True)])
    assert evaluate_channel("pre-release", "2026.10.0", releases).latest.tag == "2026.11.0"


# ---------------------------------------------------------------------------
# evaluate_channel — the acceptance criteria
# ---------------------------------------------------------------------------


def test_stable_never_reports_an_rc_or_nightly():
    result = evaluate_channel("stable", "2026.9.0", RELEASES)
    assert result.latest.tag == "2026.10.0"
    assert result.update_available is True
    assert [r.tag for r in result.pending] == ["2026.9.1", "2026.10.0"]


def test_stable_on_the_newest_stable_has_no_update_even_with_newer_rc_and_nightlies():
    result = evaluate_channel("stable", "2026.10.0", RELEASES)
    assert result.latest.tag == "2026.10.0"
    assert result.update_available is False
    assert result.pending == []


def test_pre_release_considers_rcs_and_stables_but_never_nightlies():
    result = evaluate_channel("pre-release", "2026.9.1", RELEASES)
    assert result.latest.tag == "2026.11.0-RC1"
    assert [r.tag for r in result.pending] == ["2026.10.0", "2026.11.0-RC1"]


def test_pre_release_prefers_the_final_release_over_its_rc():
    releases = classify_releases([_gh("2026.11.0-RC2", prerelease=True), _gh("2026.11.0"), _gh("2026.11.0-RC1", prerelease=True)])
    result = evaluate_channel("pre-release", "2026.11.0-RC1", releases)
    assert result.latest.tag == "2026.11.0"
    assert [r.tag for r in result.pending] == ["2026.11.0-RC2", "2026.11.0"]


def test_nightly_considers_only_nightlies_compared_by_date():
    result = evaluate_channel("nightly", "nightly-20261009-abcdef0", RELEASES)
    assert result.latest.tag == "nightly-20261017"
    assert result.update_available is True
    assert [r.tag for r in result.pending] == ["nightly-20261017"]


def test_nightly_up_to_date():
    result = evaluate_channel("nightly", "nightly-20261017", RELEASES)
    assert result.update_available is False


def test_nightly_dates_are_not_compared_like_calver_patch_numbers():
    # By numbers 2026.10.8 > 2026.10.0, but the nightly of 2026-10-08 was built
    # *before* the release of the same evening: it is not an update.
    releases = classify_releases([_gh("nightly-20261008", prerelease=True), _gh("2026.10.0", published="2026-10-08T19:40:34Z")])
    assert evaluate_channel("stable", "2026.10.0", releases).update_available is False
    assert evaluate_channel("nightly", "2026.10.0", releases).update_available is False


def test_switching_from_stable_to_nightly_compares_publication_dates():
    result = evaluate_channel("nightly", "2026.10.0", RELEASES)
    assert result.latest.tag == "nightly-20261017"
    assert result.update_available is True
    assert [r.tag for r in result.pending] == ["nightly-20261009", "nightly-20261017"]


def test_switching_from_nightly_to_stable_compares_publication_dates():
    newer = evaluate_channel("stable", "nightly-20261001", RELEASES)
    assert newer.update_available is True
    assert [r.tag for r in newer.pending] == ["2026.10.0"]
    older = evaluate_channel("stable", "nightly-20261009", RELEASES)
    assert older.update_available is False


def test_unknown_installed_version_yields_no_verdict():
    result = evaluate_channel("stable", "dev-version", RELEASES)
    assert result.latest.tag == "2026.10.0"
    assert result.update_available is None
    assert result.pending == []


def test_channel_without_releases_yields_no_verdict():
    result = evaluate_channel("nightly", "2026.10.0", classify_releases([_gh("2026.10.0")]))
    assert (result.latest, result.update_available, result.pending) == (None, None, [])


# ---------------------------------------------------------------------------
# is_newer — the cross-family edge cases
# ---------------------------------------------------------------------------


def _release(tag, published):
    return Release(version=parse_version(tag), published_at=published, url="", body="", name=tag)


def test_is_newer_nightly_installed_and_release_without_publication_date():
    assert is_newer(_release("2026.10.0", None), parse_version("nightly-20261001"), []) is None


def test_is_newer_versioned_installed_not_in_release_list():
    nightly = _release("nightly-20261017", datetime(2026, 10, 17, tzinfo=UTC))
    assert is_newer(nightly, parse_version("2026.11.0"), [nightly]) is None


def test_is_newer_versioned_installed_whose_release_lacks_a_date():
    nightly = _release("nightly-20261017", datetime(2026, 10, 17, tzinfo=UTC))
    installed_release = _release("2026.10.0", None)
    assert is_newer(nightly, parse_version("2026.10.0"), [installed_release, nightly]) is None


def test_is_newer_finds_the_installed_rc_whatever_its_case():
    nightly = _release("nightly-20261017", datetime(2026, 10, 17, tzinfo=UTC))
    installed_release = _release("2026.10.0-RC1", datetime(2026, 10, 10, tzinfo=UTC))
    assert is_newer(nightly, parse_version("2026.10.0-rc1"), [installed_release, nightly]) is True


# ---------------------------------------------------------------------------
# update_check configuration section
# ---------------------------------------------------------------------------


def test_update_check_settings_defaults_and_repository_validation():
    from pydantic import ValidationError

    from obs.config import UpdateCheckSettings

    settings = UpdateCheckSettings()
    assert (settings.enabled, settings.repository, settings.api_url) == (True, "abeggled/openbridgeserver", "https://api.github.com")
    assert UpdateCheckSettings(repository="me/fork-1.x").repository == "me/fork-1.x"
    for bad in ("no-slash", "a/b/c", "a/b?x=1", "../etc/passwd"):
        with pytest.raises(ValidationError):
            UpdateCheckSettings(repository=bad)
