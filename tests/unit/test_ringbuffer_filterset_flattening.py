"""Unit tests for the flat filterset schema helpers (#431).

Covers the pure transformation helpers in :mod:`obs.api.v1.ringbuffer`
without spinning up the full FastAPI stack:

* The :class:`FilterCriteria` → :class:`RingBufferQueryV2` translator that
  the multi-set query and single-set query endpoints use to call the
  underlying ringbuffer.
* Color validation on the public model.
"""

from __future__ import annotations

import pytest

from obs.api.v1.ringbuffer import (
    FilterCriteria,
    NodeRef,
    RingBufferTimeFilterV2,
    RingBufferValueFilterV2,
    _filter_to_query_v2,
)


@pytest.mark.parametrize(
    "color, ok",
    [
        ("#3b82f6", True),
        ("#abc", True),
        ("#3B82F6FF", True),
        ("not-a-color", False),
        ("#XYZ", False),
        ("", False),
        ("3b82f6", False),
    ],
)
def test_color_validation(color, ok):
    from obs.api.v1.ringbuffer import RingBufferFiltersetIn
    from pydantic import ValidationError

    if ok:
        assert RingBufferFiltersetIn(name="x", color=color).color == color
    else:
        with pytest.raises(ValidationError):
            RingBufferFiltersetIn(name="x", color=color)


# ---------------------------------------------------------------------------
# FilterCriteria → RingBufferQueryV2 translation (used by the multi-query
# endpoint and the single-set query endpoint).
# ---------------------------------------------------------------------------


def test_filter_to_query_v2_maps_datapoints_and_adapters_to_or_lists():
    criteria = FilterCriteria(adapters=["api", "knx"], datapoints=["dp-1", "dp-2"])
    query = _filter_to_query_v2(criteria, None)
    assert query.filters.adapters is not None
    assert query.filters.adapters.any_of == ["api", "knx"]
    assert query.filters.datapoints is not None
    assert query.filters.datapoints.ids == ["dp-1", "dp-2"]


def test_filter_to_query_v2_maps_tags_to_metadata_or_list():
    criteria = FilterCriteria(tags=["a", "b"])
    query = _filter_to_query_v2(criteria, None)
    assert query.filters.metadata is not None
    assert query.filters.metadata.tags_any_of == ["a", "b"]


def test_filter_to_query_v2_propagates_value_filter():
    criteria = FilterCriteria(
        value_filter=RingBufferValueFilterV2(operator="gt", value=42),
    )
    query = _filter_to_query_v2(criteria, None)
    assert query.filters.values is not None
    assert len(query.filters.values) == 1
    assert query.filters.values[0].operator == "gt"
    assert query.filters.values[0].value == 42


def test_filter_to_query_v2_merges_time_filter():
    criteria = FilterCriteria(datapoints=["dp-1"])
    time = RingBufferTimeFilterV2(from_ts="2024-01-01T00:00:00Z")
    query = _filter_to_query_v2(criteria, time)
    assert query.filters.time is not None
    assert query.filters.time.from_ts == "2024-01-01T00:00:00Z"


def test_filter_to_query_v2_empty_criteria_yields_empty_filters():
    """An empty criteria must produce a query that returns the unfiltered feed."""
    query = _filter_to_query_v2(FilterCriteria(), None)
    assert query.filters.adapters is None
    assert query.filters.datapoints is None
    assert query.filters.metadata is None
    assert query.filters.values is None
    assert query.filters.time is None
    assert query.filters.q == ""


def test_filter_to_query_v2_q_is_forwarded():
    criteria = FilterCriteria(q="kitchen")
    query = _filter_to_query_v2(criteria, None)
    assert query.filters.q == "kitchen"


def test_filter_criteria_accepts_hierarchy_node_refs():
    """hierarchy_nodes is stored verbatim — server-side expansion is the UI's job."""
    criteria = FilterCriteria(
        hierarchy_nodes=[NodeRef(tree_id="knx-funcs", node_id="node-1", include_descendants=True)],
    )
    assert len(criteria.hierarchy_nodes) == 1
    assert criteria.hierarchy_nodes[0].tree_id == "knx-funcs"
    assert criteria.hierarchy_nodes[0].include_descendants is True


def test_filter_criteria_forbids_extras():
    """Typos in criteria field names must fail validation, not silently drop."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FilterCriteria.model_validate({"datapoint_ids": ["dp-1"]})  # noqa: typo
