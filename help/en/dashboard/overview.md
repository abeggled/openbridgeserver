---
title: Overview
---

# Overview

The Overview is the Admin-GUI's landing page and shows the current system status at a
glance. Live Values and WS Status update live over the WebSocket connection; the other
figures (Data Points, Active Adapter Instances, Server) are loaded when the page opens and
only refresh on a reload.

## Data Points {#dashboard-stats-datapoints}

Number of all created data points — regardless of whether they currently have adapter
bindings or not.

## Active Adapter Instances {#dashboard-stats-adapters}

Number of adapter instances currently connected — a running instance that isn't (yet)
connected (e.g. mid-reconnect) doesn't count here. Details on individual instances —
including warnings and errors — are under **Adapters**.

## WS Status {#dashboard-stats-wsstatus}

Status of the Admin-GUI's WebSocket connection to the server (**Live** / **Offline**).
While "Offline", live values and status on this page stop updating automatically;
reloading the page re-establishes the connection.

## Server {#dashboard-stats-server}

The server's basic health status (**Online** / **Error**) from its health check.

## Active Warnings {#dashboard-warnings}

Only shown when at least one adapter is in a **Warning** or **Error** state — otherwise the
whole section stays hidden. Each entry shows the adapter's name, type, and severity; clicking
it leads to the adapter list under **Adapters**, where the cause can be investigated in detail.

## Monitor / Retention {#dashboard-ringbuffer}

A compact excerpt from the Monitor — full details and configuration via "To Monitor →".

- **Budget usage** — currently used storage, shown against the configured maximum where one
  is set. Without a configured maximum this reads "unlimited".
- **Segments** — number of ring buffer segments currently stored.
- If overall retention is configured as unlimited, a separate note appears for this, since
  it's operationally relevant (unbounded storage growth over time).
- If the Monitor is disabled, no value changes are recorded — "Configure" enables it directly
  from here (visible to admins only).
- "Segment Details" opens the full segment list in a dialog without leaving the Overview.

## Adapter Status {#dashboard-adapters}

Shows all configured adapter instances with a colored status dot, badge, and binding count.
The status dot summarizes:

| Color | Meaning |
|---|---|
| gray | instance inactive/stopped |
| green | running and connected |
| yellow, pulsing | running but not (yet) connected |
| yellow | warning |
| red | error |

"All →" leads to the full adapter list under **Adapters**.

## Live Values {#dashboard-values}

The last known values of the first ten data points, including their MQTT topic and quality
(**Good** / **Unknown** / **Bad**). Values updated over the WebSocket connection since the page
loaded are highlighted. "All →" leads to the full, searchable and filterable data point list
under **Data Points**.
