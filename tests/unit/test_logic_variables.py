"""Shared ###VAR### resolver for logic blocks (#1301): resolver, extractors, API Client."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from obs.logic.executor import GraphExecutor
from obs.logic.manager import _make_api_client_variable_resolver, _replace_api_client_url_placeholders
from obs.logic.models import FlowData
from obs.logic.variables import (
    VariableError,
    make_obs_resolver,
    make_time_snapshot,
    normalise_variable_slots,
    resolve_template,
    unknown_placeholders,
    value_to_string,
)
from tests.unit.conftest import make_executor, node

# 2026-06-08 05:05:09 UTC == 07:05:09 Europe/Zurich (CEST); 2026-01-05 05:05:09 UTC == 06:05:09 CET.
SUMMER = datetime(2026, 6, 8, 5, 5, 9, tzinfo=UTC)
WINTER = datetime(2026, 1, 5, 5, 5, 9, tzinfo=UTC)
CONFIG = {"timezone": "Europe/Zurich", "language": "de"}


def _resolve(template, now=SUMMER, config=None, obs=None):
    return resolve_template(template, make_time_snapshot(config or CONFIG, now), obs)


class TestResolver:
    def test_all_documented_date_tokens(self):
        r = _resolve("###H###|###HH###|###m###|###mm###|###s###|###ss###|###d###|###dd###|###EE###|###EEE###|###EEEE###")
        assert r.text == "7|07|5|05|9|09|8|08|Mo|Mo.|Montag"
        r = _resolve("###M###|###MM###|###MMM###|###MMMM###|###yy###|###yyyy###")
        assert r.text == "6|06|Juni|Juni|26|2026"

    def test_standard_variables(self):
        r = _resolve("###DATE### ###TIME### ###TS###")
        assert r.text == "08.06.2026 07:05:09 2026-06-08T07:05:09+02:00"

    def test_standard_variables_follow_configured_formats(self):
        r = _resolve("###DATE### ###TIME###", config={**CONFIG, "date_format": "yyyy-MM-dd", "time_format": "H:mm"})
        assert r.text == "2026-06-08 7:05"

    def test_minute_and_month_case_matters(self):
        assert _resolve("###m###/###M###").text == "5/6"

    @pytest.mark.parametrize(
        ("now", "expected"),
        [(SUMMER, "07"), (WINTER, "06")],
    )
    def test_dst_uses_application_timezone(self, now, expected):
        assert _resolve("###HH###", now).text == expected

    @pytest.mark.parametrize(
        ("hour_utc", "expected"),
        [(22, "00"), (23, "01"), (21, "23")],
    )
    def test_midnight_edges(self, hour_utc, expected):
        # UTC+2 in June: 22:00Z == 00:00 local, 23:00Z == 01:00, 21:00Z == 23:00
        now = datetime(2026, 6, 8, hour_utc, 0, tzinfo=UTC)
        assert _resolve("###HH###", now).text == expected

    def test_invalid_timezone_falls_back_to_utc(self):
        assert _resolve("###HH###", config={"timezone": "Nope/Nowhere"}).text == "05"

    def test_unknown_placeholders_stay_literal_and_are_reported(self):
        r = _resolve("a###FOO###b###HH###c###DP###")
        assert r.text == "a###FOO###b07c###DP###"
        assert r.unknown == ["FOO", "DP"]
        assert r.ok

    def test_unknown_placeholders_are_unique(self):
        assert unknown_placeholders("###X######X###") == ["X"]

    def test_obs_values_are_not_expanded_recursively(self):
        r = _resolve("###OBS1###", obs=lambda i: "###HH###")
        assert r.text == "###HH###"

    def test_obs_without_resolver_is_error(self):
        r = _resolve("x###OBS2###")
        assert not r.ok
        assert r.errors == ["variable OBS2 is not configured"]

    def test_quote_applies_to_values_only(self):
        r = resolve_template("a b/###OBS1###", make_time_snapshot(CONFIG, SUMMER), lambda i: "x y", quote=lambda v: v.replace(" ", "%20"))
        assert r.text == "a b/x%20y"

    def test_quote_applies_to_date_values(self):
        r = resolve_template("###TS###", make_time_snapshot(CONFIG, SUMMER), quote=lambda v: v.replace(":", "_"))
        assert r.text == "2026-06-08T07_05_09+02_00"


class TestObsResolver:
    def test_values_cached_and_converted(self):
        calls = []

        def lookup(dp):
            calls.append(dp)
            return {"a": [1]}

        resolver = make_obs_resolver([{"slot": 1, "datapoint_id": "dp1", "datapoint_name": "N"}], lookup)
        assert resolver(1) == '{"a": [1]}'
        assert resolver(1) == '{"a": [1]}'
        assert calls == ["dp1"]

    def test_unconfigured_slot(self):
        with pytest.raises(VariableError, match="OBS4 is not configured"):
            make_obs_resolver([], lambda dp: 1)(4)

    def test_no_lookup(self):
        resolver = make_obs_resolver([{"slot": 1, "datapoint_id": "dp1", "datapoint_name": "N"}], None)
        with pytest.raises(VariableError, match="N is not available"):
            resolver(1)

    def test_missing_value(self):
        resolver = make_obs_resolver([{"slot": 1, "datapoint_id": "dp1", "datapoint_name": "N"}], lambda dp: None)
        with pytest.raises(VariableError, match="N has no value"):
            resolver(1)

    def test_lookup_failure_is_invalid_object(self):
        def boom(dp):
            raise ValueError("bad uuid")

        resolver = make_obs_resolver([{"datapoint_id": "dp1"}], boom)
        with pytest.raises(VariableError, match="invalid object"):
            resolver(1)

    def test_slot_normalisation(self):
        raw = json.dumps(
            [{"slot": "x", "datapoint_id": "a"}, {"slot": 0, "datapoint_id": "b"}, {"slot": 5}, "junk", {"slot": 7, "datapoint_id": "c"}]
        )
        assert set(normalise_variable_slots(raw)) == {1, 2, 7}
        assert normalise_variable_slots("not json") == {}
        assert normalise_variable_slots({"a": 1}) == {}

    def test_value_to_string(self):
        assert value_to_string(True) == "true"
        assert value_to_string(3) == "3"
        with pytest.raises(VariableError):
            value_to_string(None)


def _executor(nodes, lookup=None, now=SUMMER, config=None):
    flow = FlowData.model_validate({"nodes": nodes, "edges": []})
    return GraphExecutor(flow, app_config=config or CONFIG, run_time=now, datapoint_lookup=lookup)


HOURS = json.dumps([{"text": {"value": f"h{h}"}} for h in range(24)])


class TestJsonExtractorVariables:
    def test_hour_index_in_application_timezone(self):
        n = node("j", "json_extractor", {"json_path": "[###H###].text.value"})
        out = _executor([n]).execute({"j": {"data": HOURS}})["j"]
        assert out["value"] == "h7"
        assert out["_resolved_paths"] == ["[7].text.value"]
        assert out["_path_templates"] == ["[###H###].text.value"]
        assert "_issues" not in out

    @pytest.mark.parametrize(("hour_utc", "expected"), [(22, "h0"), (23, "h1"), (21, "h23")])
    def test_hour_edges(self, hour_utc, expected):
        n = node("j", "json_extractor", {"json_path": "[###H###].text.value"})
        now = datetime(2026, 6, 8, hour_utc, 0, tzinfo=UTC)
        assert _executor([n], now=now).execute({"j": {"data": HOURS}})["j"]["value"] == expected

    def test_multi_path_mode_with_date_and_obs(self):
        paths = [
            {"label": "a", "path": "forecast.###yyyy###.###MM###.###dd###"},
            {"label": "b", "path": "devices.###OBS1###.state"},
            {"label": "c", "path": "static"},
        ]
        data = {"forecast": {"2026": {"06": {"08": 21}}}, "devices": {"lamp": {"state": "on"}}, "static": 1}
        n = node(
            "j",
            "json_extractor",
            {
                "json_paths": json.dumps(paths),
                "variables": [{"slot": 1, "datapoint_id": "dp1", "datapoint_name": "Lamp"}],
            },
        )
        out = _executor([n], lookup=lambda dp: "lamp").execute({"j": {"data": json.dumps(data)}})["j"]
        assert (out["out_1"], out["out_2"], out["out_3"]) == (21, "on", 1)
        assert out["_resolved_paths"] == ["forecast.2026.06.08", "devices.lamp.state", "static"]

    def test_static_path_output_is_unchanged(self):
        n = node("j", "json_extractor", {"json_path": "a"})
        assert _executor([n]).execute({"j": {"data": '{"a": 1}'}})["j"] == {
            "value": 1,
            "_preview": '{"a": 1}',
        }

    def test_missing_obs_value_yields_null_and_issue(self):
        n = node("j", "json_extractor", {"json_path": "devices.###OBS1###", "variables": [{"slot": 1, "datapoint_id": "dp1"}]})
        out = _executor([n], lookup=lambda dp: None).execute({"j": {"data": '{"devices": {"x": 1}}'}})["j"]
        assert out["value"] is None
        assert any("has no value" in i for i in out["_issues"])

    def test_unconfigured_slot_yields_null_and_issue(self):
        n = node("j", "json_extractor", {"json_path": "devices.###OBS3###"})
        out = _executor([n]).execute({"j": {"data": '{"devices": {"x": 1}}'}})["j"]
        assert out["value"] is None
        assert out["_issues"] == ["variable OBS3 is not configured"]

    def test_unknown_variable_stays_literal_and_is_flagged(self):
        n = node("j", "json_extractor", {"json_path": "a.###FOO###"})
        out = _executor([n]).execute({"j": {"data": '{"a": {"###FOO###": 5}}'}})["j"]
        assert out["value"] == 5
        assert out["_issues"] == ["unknown variable ###FOO###"]

    def test_unresolvable_path_gives_null_with_hint(self):
        n = node("j", "json_extractor", {"json_path": "[###H###].missing"})
        out = _executor([n]).execute({"j": {"data": HOURS}})["j"]
        assert out["value"] is None
        assert out["_issues"] == ["path '[7].missing' not found"]

    def test_multi_path_unresolvable_path_gives_null_with_hint(self):
        n = node("j", "json_extractor", {"json_paths": json.dumps([{"path": "[###H###].missing"}, "junk"])})
        out = _executor([n]).execute({"j": {"data": HOURS}})["j"]
        assert out["out_1"] is None
        assert out["out_2"] is None
        assert out["_issues"] == ["path '[7].missing' not found"]

    def test_obs_value_is_not_expanded_again(self):
        n = node("j", "json_extractor", {"json_path": "###OBS1###", "variables": [{"slot": 1, "datapoint_id": "dp1"}]})
        out = _executor([n], lookup=lambda dp: "###H###").execute({"j": {"data": '{"###H###": "literal", "7": "expanded"}'}})["j"]
        assert out["value"] == "literal"

    def test_one_time_snapshot_per_run(self):
        a = node("a", "json_extractor", {"json_path": "###ss###"})
        executor = _executor([a], now=SUMMER)
        executor.execute({})
        first = executor._time_snapshot()
        executor.execute({})
        assert executor._time_snapshot() is first


XML = "<r><hours><hour><text><value>h0</value></text></hour><hour><text><value>h1</value></text></hour></hours><f hour='07'><value>seven</value></f><d id='lamp'><state>on</state></d></r>"


class TestXmlExtractorVariables:
    def test_hour_in_attribute_predicate(self):
        n = node("x", "xml_extractor", {"xml_path": ".//f[@hour='###HH###']/value"})
        out = _executor([n]).execute({"x": {"data": XML}})["x"]
        assert out["value"] == "seven"
        assert out["_resolved_paths"] == [".//f[@hour='07']/value"]

    def test_obs_in_multi_path(self):
        paths = [{"path": ".//d[@id='###OBS1###']/state"}, {"path": ".//f[@hour='###HH###']/value"}, {"path": ""}]
        n = node("x", "xml_extractor", {"xml_paths": json.dumps(paths), "variables": [{"slot": 1, "datapoint_id": "dp1"}]})
        out = _executor([n], lookup=lambda dp: "lamp").execute({"x": {"data": XML}})["x"]
        assert (out["out_1"], out["out_2"], out["out_3"]) == ("on", "seven", None)

    def test_multi_path_missing_obs_is_issue(self):
        n = node("x", "xml_extractor", {"xml_paths": json.dumps([{"path": ".//d[@id='###OBS2###']"}])})
        out = _executor([n]).execute({"x": {"data": XML}})["x"]
        assert out["out_1"] is None
        assert out["_issues"] == ["variable OBS2 is not configured"]

    def test_invalid_xpath_does_not_abort(self):
        n = node("x", "xml_extractor", {"xml_path": ".//a[###H###"})
        out = _executor([n]).execute({"x": {"data": XML}})["x"]
        assert out["value"] is None
        assert out["_issues"][0].startswith("invalid XPath './/a[7'")

    def test_not_found_is_null_without_issue(self):
        n = node("x", "xml_extractor", {"xml_path": ".//nothing"})
        out = _executor([n]).execute({"x": {"data": XML}})["x"]
        assert out == {"value": None, "_preview": XML}


class TestExecutorDefaults:
    def test_snapshot_without_run_time_uses_now(self):
        ex = make_executor([node("j", "json_extractor", {"json_path": "[###H###].text.value"})], app_config=CONFIG)
        out = ex.execute({"j": {"data": HOURS}})["j"]
        assert out["value"].startswith("h")
        assert out["_resolved_paths"][0].startswith("[")


class TestApiClientDateVariables:
    def test_date_variables_in_url_path_and_query(self):
        snapshot = make_time_snapshot(CONFIG, SUMMER)
        resolver = _make_api_client_variable_resolver(None, [], None, snapshot)
        url = _replace_api_client_url_placeholders("http://example.com/day/###yyyy###/###MM###?at=###TS###", resolver)
        assert url == "http://example.com/day/2026/06?at=2026-06-08T07%3A05%3A09%2B02%3A00"

    def test_date_variable_not_allowed_in_host(self):
        resolver = _make_api_client_variable_resolver(None, [], None, make_time_snapshot(CONFIG, SUMMER))
        with pytest.raises(Exception, match="not allowed in the scheme, host"):
            _replace_api_client_url_placeholders("http://example.###H###.com/x", resolver)

    def test_date_variable_without_snapshot_uses_now(self):
        resolver = _make_api_client_variable_resolver(None, [])
        assert resolver("yyyy").isdigit()
