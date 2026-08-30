---
title: History
---

# History

Shows historical values of a single data point as a chart and optionally as a table — for
tracing value trends, independent of the live value on the data point detail page. The
underlying values are stored in the ring buffer (see **Monitor**); whether and how long a
data point is historized is controlled by its "Record history" setting.

## Selection and time range {#history-controls}

- **Object** — searches name/UUID; only one object can be shown at a time.
- **From** / **To** — the time range to query.
- **Mode**:
  - **Raw** — every individually stored value, unchanged.
  - **Aggregated** — values combined into equal-length intervals (see below).
- **Function** (Aggregated only) — how values within an interval are condensed: average,
  minimum, maximum, or last value.
- **Interval** (Aggregated only) — the width of one aggregation step, from 1 minute to
  1 day. A larger interval smooths the chart and reduces the point count over long time
  ranges.

"Load" queries the values for the current selection. Changing the object clears the
display; an in-flight query is discarded if the object or selection changes while it's
running — only the most recently started query ever counts.

## Chart and raw data {#history-results}

The chart shows the loaded series over time, with the number of loaded points in the
header. In **Raw** mode, a table with every individual value also appears: timestamp,
value (including unit, if any), quality (**Good**/**Unknown**), and the adapter the value
came from. If no value exists in the selected time range, a corresponding hint appears
instead of an empty chart.
