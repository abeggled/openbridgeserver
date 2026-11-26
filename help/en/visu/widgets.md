---
title: Visu widgets
---

# Visu widgets {#visu-widgets}

Widgets are the building blocks of a Visu page. In the Visu editor you drag them from the
palette onto the page and configure them in the properties panel on the right.

## Camera {#widget-kamera}

The **Camera** widget ("Kamera") shows a camera picture. **Source** offers two kinds:

- **Camera URL** — an MJPEG stream, a JPEG still image with a refresh interval or an HLS stream
  (`.m3u8`) straight from the camera, optionally through the OBS proxy.
- **Camera server (go2rtc / Frigate)** — a live stream from an instance of the
  [GO2RTC or FRIGATE](../adapters/list#adapters-camera-servers) adapters. This also shows RTSP
  cameras a browser cannot play by itself.

### HLS streams

The widget plays an HLS stream (`.m3u8`) in all common browsers, not only in Safari. The Visu
loads the library it needs only when an HLS widget is on the page. The camera has to allow the
browser to fetch from another address (CORS); otherwise the picture stays black and the browser
console reports a CORS error. HLS cannot be played through the **proxy**, because it passes on
only the playlist, not the video segments.

### Camera server

- **Camera server**, **Camera** — the instance and, for Frigate, the camera.
- **Stream in the tile** / **Stream in full screen** — usually the sub stream in the tile and the
  main stream in full screen. For Frigate both are proposed.
- **Transport** — *Automatic* tries WebRTC (lowest latency, directly between camera server and
  browser) and falls back to MSE after a few seconds. *MSE only* skips WebRTC.

Until the stream runs, and when it drops, the widget shows a still image and reconnects by
itself. A button in the bottom right switches to full screen, and for streams with sound another
one turns the sound on. A hidden widget or a tab in the background pauses the stream. In the Visu
editor the widget shows the still image only.

### Display and tap

- **Aspect ratio**, **Image fit** — format and fit of the picture.
- **On tap** — opens another page or goes back to the previously visited page. The full-screen
  and sound buttons do not trigger it.

## Event list {#widget-terminliste}

The **Event list** ("Terminliste") shows the events of the coming days as a list, one entry per
day: **Today**, **Tomorrow** and then the date (e.g. `Wed 15.07.26`), followed by that day's
events. Events with a time start with their start time; all-day events come first. Several
calendars can be merged into one widget, each in its own colour — for example waste collection
dates in orange and private appointments in green.

### Connecting a calendar

The widget does not read a calendar URL but **objects**. The calendar is loaded on the server by
the [iCalendar](../logic/blocks-integration#logic-block-ical) Logic block. This keeps even a
private calendar address secret, because every viewer can read a Visu page's configuration.

1. Create an **object** of type `STRING`, e.g. "Calendar waste". Switch off **History active**
   for it: every refresh writes the complete event list, so its history would grow quickly.
2. In the Logic module, add an **iCalendar** block with the calendar's ICS address. In Google
   Calendar you find it in the settings of the calendar under *Integrate calendar* (*Secret
   address in iCal format*, or *Public address in iCal format* for public calendars).
3. Add a **filter** (without patterns it matches every event) and connect its **Array** output
   to the object from step 1 with a **Write Object** block. On the write block, enable **Only
   write if value has changed**. The write block stores the array in the `STRING` object as
   JSON text.
4. In the Visu editor, insert the **Event list** widget and pick the object as calendar source.

For another calendar or another category (e.g. a filter on "paper" only), write one more object
and add it as an additional source.

### Settings

- **Label** — optional title above the list.
- **Calendar sources** — up to eight objects, each with a **colour** and a **name**. The name
  appears as a tooltip on the event.
- **Number of days** — period starting today (1–366 days, default 7).
- **Separator** — separates the events of a day: semicolon, asterisk or line break (one line per
  event).
- **Date format** — format for the days after tomorrow, with the same placeholders as in
  [Timezone, date and time format](../settings/general#settings-general), e.g.
  `EEE dd.MM.yy` → `Wed 15.07.26` or `EEEE MMMM d` → `Wednesday July 15`.
- **Show start time**, **Show location** — show or hide the event's time and location.
- **Show days without events** — also lists free days (with "–") instead of skipping them.

Events are evaluated in the server's timezone. At midnight the list moves on by itself.
