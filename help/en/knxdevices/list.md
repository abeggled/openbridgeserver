---
title: KNX Devices
---

# KNX Devices

This view shows all physical KNX devices from the last imported KNX project (ETS export),
including their communication objects and group addresses — regardless of whether they
already have data point bindings in OBS.

## Device list {#knxdevices-list}

The header shows the number of imported devices and a note that this is a snapshot of the
last imported KNX project — a new import replaces this snapshot entirely. "Import KNX
project" (visible to admins only) leads to **Settings → Data Management**, where an ETS
project file is uploaded.

## Search and filters {#knxdevices-filters}

- **Search field** — searches PA (physical address), name, manufacturer, and order number.
- **Manufacturer** and **Order number** — further narrow down by exact or partial match.
- **Hierarchy** — filters to devices assigned to a specific node/branch of the data point
  hierarchy.

All filters combine (logical AND); "Search" applies them.

## Table {#knxdevices-table}

Clicking a row opens the device in the detail panel on the right. The **Hierarchies**
column shows the device's assigned hierarchy paths as chips; **Application** shows the
reference to the KNX application program ID from the ETS export.

## Device details {#knxdevices-detail}

Once a device is selected, the panel shows:

- **Basic data** — manufacturer, order number, application reference.
- **Hierarchy assignment** — which node(s)/branch(es) of the data point hierarchy this
  device is assigned to. Admins can edit and save this assignment directly here.
- **Communication objects** — each of the device's communication objects with its data
  point type (DPT) and linked group addresses. For each group address, "Bound data points"
  shows which OBS data points read (Read), write (Write), or both via it, and whether that
  binding is currently enabled or disabled.

If no device is selected yet, the panel stays empty with a corresponding hint.
