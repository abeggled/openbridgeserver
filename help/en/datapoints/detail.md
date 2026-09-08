---
title: Data Point Detail
---

# Data Point Detail

Shows a single data point in full: its live value, all properties, and every place it's
connected to — adapter bindings, Logic usage, and hierarchy assignments. Reached by clicking
a data point's name in the **Data Points** list.

## Current value {#datapoints-detail}

Shows the live value (updated over the WebSocket connection), its timestamp, and the MQTT
topic (plus alias, if set). A write control appears — a true/false toggle for BOOLEAN data
points, otherwise a text field — as soon as at least one enabled binding can accept
writes. The area is disabled only when at least one such binding exists and none of them
writes. Disabled bindings and those of the message adapter do not count: a data point that
has only those — or no bindings at all — stays writable, and its value then lives only in
OBS. Writing goes through the same path as any other write source.

## Properties {#datapoints-detail-properties}

Name, data type, unit, tags, and the "Persist value" / "Record history" settings, plus the
creation and last-update timestamps. "Edit" opens the same form used when creating a data
point; "History" jumps to the **History** page pre-filtered to this data point.

## Hierarchy assignments {#datapoints-detail-hierarchy}

Shows which hierarchy nodes this data point is currently assigned to, and lets an admin add
or remove assignments by searching the hierarchy tree — see the hierarchy documentation
under **Settings** for how the hierarchy itself is managed.

## Adapter bindings {#datapoints-detail-bindings}

Every adapter binding for this data point, with its direction (read/write/read-write) and
enabled state. A KNX binding additionally shows the group address(es) involved, the KNX
device(s) known to send or receive on them, and their communication objects, where that
context is available. "Add binding" opens the binding form for a new adapter connection;
each existing binding can be edited or deleted from here.

## Logic bindings {#datapoints-detail-logic}

Every Logic graph node that reads from or writes to this data point, with a link that opens
the graph in the **Logic** editor.
