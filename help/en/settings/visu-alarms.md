---
title: Visu alarms
---

# Visu alarms

The tab lives in the Admin GUI under **Settings → Visu alarms**. Only administrators see it.

## Bring messages and pages to the foreground {#settings-visu-alarms}

A Visu alarm brings a message or a Visu page to the foreground on all or selected Visu devices
when something happens — for example "Mail has arrived" when the mailbox contact triggers, or a
notice for a phone call. The alarm appears on top of every page and on the overview, with or
without login.

Each alarm has:

| Area | Setting |
|---|---|
| **Trigger** | Datapoint, condition (`==`, `!=`, `<`, `>`, `contains` …) and compare value, e.g. `== true`. The alarm starts as soon as the condition **becomes** true. If it was already true when saving or when the server started, it starts on the next change. |
| **Display** | **Message window** with title, text, icon and urgency (info blue, warning orange, critical red) — or **Visu page as popup**: a page designed in the editor appears as a window, with all widgets and live values. |
| **End** | **"Acknowledge" button** — ends the alarm on every device — and/or **automatically** as soon as the condition is no longer met (e.g. the phone stops ringing). At least one must be chosen. |
| **Sound** | Optionally a short signal tone. Browsers only play sounds after the page was touched or clicked once. |
| **Devices** | The [Visu devices](./visu-devices) the alarm appears on. Nothing ticked = on every Visu. A renamed or deleted device stays ticked in red until you untick it; saving is blocked until then. |

**Save** stores all alarms at once; invalid alarms are marked and block saving. A disabled alarm
stays stored but does not trigger.

### Details

- With several active alarms the Visu shows the newest; a note says how many are waiting. After
  acknowledging, the next one appears.
- A page protected by a PIN or a login asks for it in the popup as usual.
- Without login the server only sends what the alarm defines for display (title, text, icon,
  page) — no datapoint values. Acknowledging works without login too, so it works on a wall panel; it is only
  accepted from the Visu itself (not from other web pages) and is written to the audit log with the
  device's address.
- Alarms are part of the app settings and therefore included in configuration export, backups and
  factory reset. Active alarms end when the server restarts.
