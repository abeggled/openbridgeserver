---
title: Visu devices
---

# Visu devices

The tab lives in the Admin GUI under **Settings → Visu devices**. Only administrators see it.

## Start page and return per device {#settings-visu-devices}

A Visu often runs on several devices at once — a phone, a wall panel in the hallway, a desktop
PC. Each device should open a different page, and a wall panel should return to its start page
on its own after a while without use. That is what **device profiles** are for.

Each profile has:

| Field | Meaning |
|---|---|
| **Name** | Free text such as "Hallway wall panel". Shown in the Visu's device selector. |
| **Identifier** | Short name for the link: lowercase letters, digits and hyphens only (e.g. `hallway-panel`). Suggested while you type the name. |
| **Start page** | The page the Visu opens with on this device. "Overview" keeps the previous behaviour. |
| **Return after** | Seconds without touch, mouse, wheel or keyboard input after which the Visu goes back to the start page. `0` = never. |

**Save** stores all profiles at once. Rows with errors (empty name, invalid or duplicate
identifier, a time outside 0–86400 seconds, a deleted start page) are marked and block saving.

### Assigning a device to a profile

The profile is chosen **on the device itself** and stored in its browser:

- **Via link:** every row shows the profile's link, e.g.
  `http://obs.local:8080/visu/?device=hallway-panel`. Open it once on the device or use it as a
  bookmark or kiosk start URL. `?device=none` releases the device from its profile.
- **Via selector:** as soon as profiles exist, the Visu overview shows a **Device** selector in
  the top right.

From then on `/visu/` opens the profile's start page on that device. Changes to the start page or
return time in the Admin GUI apply the next time the Visu is loaded on the device.

### Details

- The Visu only returns from the normal view (a page or the overview) — never from the editor,
  the page management or a login or PIN prompt.
- A start page protected by a PIN or login still asks for it when opened. The profile itself
  holds no credentials.
- If the start page is deleted, the device opens the overview instead; the row in the tab stays
  marked until another page is chosen.
- Profiles are part of the app settings and therefore included in configuration export, backups
  and factory reset.
