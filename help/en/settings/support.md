---
title: Support
---

# Support

The Support tab lives in the Admin GUI under **Settings → Support**. This tab is only
visible and usable for administrators.

## Debug log settings {#settings-support-debug}

Enables more detailed local logging for **exactly 5 minutes**, so the extra diagnostic
entries are included in the support package created afterward. Typical flow: enable debug,
reproduce the problem, then create the support package — the in-memory log buffer is carried
into the package.

This **sends nothing and opens no remote access** — everything stays local. After the 5
minutes expire, or when disabled manually, the previous log level is automatically restored.

## Create support package {#settings-support-package}

Generates a diagnostics package (JSON file) with system information, adapter status,
history/monitor statistics, and warnings — generated and downloaded locally, **never sent
automatically**. Sensitive values (passwords, tokens, etc.) are centrally stripped before
export.

## Analyze support package {#settings-support-viewer}

Opens a previously downloaded `obs_support` JSON file locally in the browser to inspect it in
a structured way — installation data, runtime/resources, adapter overview, history/monitor
metrics, and a searchable list of warnings and debug log entries. The file is **neither
uploaded nor stored** — the analysis happens entirely in the browser. Useful for inspecting a
support package received from another system without having access to that system.
