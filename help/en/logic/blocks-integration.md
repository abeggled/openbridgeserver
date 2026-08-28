---
title: "Blocks: Integration"
---

# Blocks: Integration

Blocks for connecting to external systems: network actions, HTTP APIs, calendars, and
extracting values from structured text formats.

## Wake on LAN {#logic-block-wake-on-lan}

Sends a Wake-on-LAN magic packet via UDP broadcast as soon as the **Trigger** input becomes
true. **MAC address**, **broadcast IP**, and **UDP port** are validated directly in the
config panel (invalid values are highlighted in red with an error message).

## Host Check (Ping) {#logic-block-host-check}

Pings a **host**/IP address and returns **Reachable** (bool) and **Latency (ms)**. Fires on a
rising edge on the **Trigger** input — recommendation: connect it to a Timer/Cron block for
periodic checks. **Timeout** and **ping count** are configurable.

## JSON Extractor {#logic-block-json-extractor}

Parses a JSON string (**Data** input) and extracts values via dot-notation key paths (e.g.
`sensors.temperature`). Use **+** to add multiple named outputs; each row shows a live preview
of the extracted value based on the most recently received data. A detected-paths dropdown
(from the most recently received data) auto-fills the currently active output row. An older
single-path configuration is shown as a legacy notice with a one-click upgrade to multiple
outputs.

## XML Extractor {#logic-block-xml-extractor}

Parses an XML string (**Data** input) and extracts values via XPath expressions in
ElementTree syntax (e.g. `.//temperature`). Handling is identical to the JSON Extractor:
multiple named outputs via **+**, per-row live preview, a detected-paths dropdown, and a
legacy-upgrade banner for an existing single-path configuration.

## Substring / RegEx {#logic-block-substring-extractor}

Extracts text from a string (**Data** input). The **mode** determines which further fields
appear:

- **before / after**: text before/after a **search term**, at either the first or last
  occurrence.
- **between**: text between a **start** and an **end marker**.
- **cut**: a fixed **start position** (0-based) and **length** (-1 = to the end).
- **regex**: a Python **regex pattern** with optional **flags** (e.g. `i` for
  case-insensitive) and a selectable **capture group** (0 = entire match); a link opens
  regex101.com for testing.

A test area shows the live result for the most recently received data, or manually entered
test text.

## iCalendar {#logic-block-ical}

Periodically loads an iCal/ICS file from a **URL** (**refresh interval** in minutes,
**maximum calendar size** as a guard against oversized downloads) and evaluates its events.
The **RAW** output provides the raw calendar text independent of any filters.

**Add filter** lets you define any number of named filters; each filter produces 4 outputs
(array of all matching events, next date, tomorrow as bool, today as bool). A filter can apply
regular expressions to **summary**, **location**, and/or **description**, combined with
**AND**/**OR**, and can optionally be case-sensitive. An empty pattern field is ignored (not
treated as an exclusion criterion).

## API Client {#logic-block-api-client}

Sends HTTP requests (GET/POST/PUT/PATCH/DELETE) to a configurable **URL**; the **Trigger**
input fires the request. The **Check target** button shows in advance whether the configured
URL is allowed or would be blocked by the server-side SSRF guard (administrators can allow a
blocked target directly from this dialog).

**Variables** let you insert data points as placeholders (`###OBS1###`, `###OBS2###`, …) into
the URL or body — their current values are substituted in before the request is sent. Further
settings: request/response content type, custom headers (as a JSON object or via a header file
under `/run/secrets`), timeout, SSL certificate verification, and authentication (none, Basic,
Digest, or a bearer token — also loadable from a file under `/run/secrets`). Outputs:
**Response**, **Status** (HTTP status code), and **Success** (fires on a 2xx response).
