---
title: Adapter Instances
---

# Adapter Instances {#adapters}

Adapters connect external systems (KNX, Modbus, MQTT, 1-Wire, Home Assistant, ioBroker,
SNMP, scheduling, presence simulation, and more) to OBS as **instances**. Each instance has
a type, its own configuration, and any number of bindings to data points.

## Instance list {#adapters-list}

Each card shows an adapter instance with:

- **Status dot** — summarizes the connection state by color:

  | Color | Meaning |
  |---|---|
  | gray | instance inactive/stopped |
  | green | running and connected |
  | yellow, pulsing | running but not (yet) connected |
  | yellow | warning (degraded operation) |
  | red | error |

- **Type badge** — the adapter type (e.g. KNX, MODBUS_TCP).
- **Status badge** — text form of the status dot (Connected / Running / Degraded /
  Inactive / Error).
- **Bindings** — number of data point bindings this instance has.

On warning or error, a detail message with the exact cause appears as well. Clicking the
arrow on the right expands the instance to show its configuration and actions (see below).

## Create a new instance {#adapters-create}

"+ New instance" opens a form: first choose the **adapter type** and **name**, then the
type-specific configuration mask appears (e.g. host/port for KNX or Modbus TCP, broker
address for MQTT). Bindings to data points can only be created once the instance exists.

## Instance actions {#adapters-instance-actions}

When an instance is expanded:

- **Test connection** — checks the currently entered configuration without saving.
- **Save** — applies changes and reconnects the adapter.
- **Reconnect** — disconnects and reconnects using the existing configuration, without
  changing it.
- **Import** (ioBroker and Home Assistant) — imports states or entities as new OBS objects
  with a binding; for Home Assistant see [Home Assistant](#adapters-homeassistant).
- **Manage objects** (presence simulation only) — selects simulated Boolean/Integer objects
  and manages their bindings.
- **Migrate bindings** — moves all of this instance's bindings to another instance of the
  same adapter type; bindings already present at the target are skipped.
- **Delete instance** — deletes the instance irreversibly, including all of its bindings.

"Enabled" turns the instance off entirely without deleting it — a disabled instance keeps
its configuration and bindings but does not connect.

## Home Assistant {#adapters-homeassistant}

The `HOME_ASSISTANT` adapter connects an existing Home Assistant instance. Home Assistant stays
the runtime for the device integrations (e.g. Sonos, Velux); OBS stays in charge of objects,
logic, permissions, history and visualization. Several instances are possible.

### Connection and token

- **Host/port/SSL** — address of the Home Assistant instance (default port 8123).
- **Access token** — a *long-lived access token* from the Home Assistant profile. A regular
  user is enough for entity states and actions. Home Assistant only returns device, area and
  integration data in the entity browser to an **administrator** token; without it everything
  else works unchanged and the browser says so.
- The token is only sent to Home Assistant, never logged, and never shown to users without
  administrator rights in OBS.

The status only switches to *Connected* after a successful login. OBS keeps a single
WebSocket connection per instance, checks it with a ping every 30 s, and reconnects after a
failure with a growing delay (1 s to 60 s). An invalid token is shown as *Token invalid*.

### Synchronization and quality

OBS only subscribes to the entities needed by reading bindings (source/both). After every
connect and every change of the bindings, Home Assistant sends a full snapshot: a new binding
gets its value immediately, and changes during a connection loss are not missed.

| Home Assistant state | Value in OBS | Quality |
|---|---|---|
| normal state | new value | good |
| `unknown`, transitional state (e.g. `locking`, `opening`) | last stable value | uncertain |
| `unavailable`, fault state (e.g. `jammed`), entity removed | last stable value | bad |
| connection to Home Assistant lost | last stable value | bad |

After an OBS restart the object's stored value counts as the last stable value, even while
Home Assistant is unreachable. Changing bindings does not re-send unchanged values to other
adapters.

Only values with quality *good* are passed on to other bindings.

### Importing entities

**Import** on the instance opens the entity browser of the connected instance:

1. Search entities and filter by domain, area, integration, device and availability.
2. Tick the proposed entry of an entity, or expand it and choose precisely: the state with a
   matching **profile**, the state without a profile (read only), or individual
   **attributes** (e.g. `current_position`, `volume_level`). An attribute is always imported
   read only; to control the device tick the matching profile (e.g. *Position*).
3. **Preview** shows without any change what would be created: name, data type, unit,
   direction, tags, read mapping, actions and the Boolean convention (e.g. *true = locked*).
4. The **name** of a new object can be changed right in the preview; the arrow icon restores the
   proposal.
5. **Import** only creates entries with status *new*.

The import can be repeated: entities already bound show as *exists* and are skipped. If an
entity was renamed in Home Assistant, OBS recognizes it by its registry identity and reports a
*conflict* instead of creating a duplicate. It is also a *conflict* when the same state or
attribute is already bound manually (without a profile; write-only bindings do not count) or
appears twice in the same selection. Existing objects and bindings are never overwritten. The
data type of an imported state follows its current value (`on`/`off` → Boolean, number →
number, otherwise text), and the binding later reads the value as exactly that type.

### Profiles

A profile declares how a state is read and which Home Assistant action a write triggers —
without templates or expressions.

| Profile | OBS value | Action | Read |
|---|---|---|---|
| Locked (`lock`) | true/false | `lock.lock` / `lock.unlock` | `locked` → true; `unlocked`, `open` → false |
| Open (`lock`) | command | `lock.open` | — |
| Open/closed (`cover`, `valve`) | true/false | `open_cover`/`close_cover` or `open_valve`/`close_valve` | `open` → true; `closed` → false |
| Position (`cover`, `valve`) | 0–100 | `set_cover_position` / `set_valve_position`, field `position` | attribute `current_position` |
| Stop (`cover`, `valve`, `media_player`) | command | `stop_cover` / `stop_valve` / `media_stop` | — |
| On/off (`switch`, `input_boolean`, `fan`, `light`) | true/false | `turn_on` / `turn_off` | `on` → true |
| Brightness (`light`) | 0–255 | `light.turn_on`, field `brightness` | attribute `brightness`; off → 0 |
| Number value (`number`, `input_number`) | number | `set_value`, field `value` | state |
| Selected option (`select`, `input_select`) | text | `select_option`, field `option` | state |
| Text (`text`, `input_text`) | text | `set_value`, field `value` | state |
| Press (`button`) / scene (`scene`) | command | `button.press` / `scene.turn_on` | — |
| Media player | state, volume (0–1), mute, source, title, artist, play/pause, power, next/previous | `media_play`/`media_pause`, `volume_set`, `volume_mute`, `select_source`, … | state or attributes |

**Command profiles** only trigger their action on `true`; `false` does nothing. On a numeric
object 1 and 0 count as `true` and `false`; a value without a mapped action counts as a failed
write. When a profile's data type does not fit the object, the binding form says so. A profile is
only offered when the entity reports the required feature. Ambiguous domains (alarm panel,
climate, vacuum, lawn mower, media playback versus power) never get a silent write mapping:
without an explicitly chosen profile they are imported read only.

Examples: a **Velux** roof window becomes fully controllable with *Open/closed*, the attribute
`current_position` (profile *Position*) and *Stop*. A **Sonos** player with *State*, *Volume*,
*Mute*, *Play/pause* and *Next*/*Previous*.

### Editing a binding

The binding form shows and edits the profile, the attribute and the **resolved mapping**.
Entity ID, attribute, service domain, service name and service data key are searchable: while
typing, OBS suggests the entities, attributes, domains, actions and data fields of the selected,
connected instance. Free input stays possible, e.g. while Home Assistant is unreachable. The
entity browser in the import dialog also searches while you type. The
manual fields *Service domain*, *Service name* and *Service data key* work as before and take
precedence over a profile — existing bindings therefore behave unchanged, no migration is
needed. *Advanced mapping (JSON)* additionally accepts:

- `action` — one action `{"domain", "name", "target", "data", "value_path"}`; the object's
  value goes into the field `value_path`;
- `write_actions` — value-dependent actions, e.g.
  `{"true": {"domain": "lock", "name": "lock"}, "false": {"domain": "lock", "name": "unlock"}}`;
- `state_map`, `transitional_states`, `fault_states` — custom read normalization.

**Read as** sets the type the value is converted to (Boolean, number, text or automatic); a
value that does not fit keeps the last value with quality *uncertain*. A profile must fit the
direction: a read-only profile cannot write and a command profile cannot read — OBS rejects such
bindings when saving. Invalid JSON in the advanced mapping prevents saving.

The action is chosen from the typed object value; the binding's formula and value map only
change the value sent, never which action runs.

### Limitations and troubleshooting

- If Home Assistant rejects an action, the write counts as failed and the instance shows the
  warning *Home Assistant rejected …* until an action succeeds again.
- Complex actions (e.g. `play_media`, grouping, queues) and action responses are not mapped.
- *Token invalid*: create a new token in Home Assistant and enter it in the instance.
- Entity IDs must follow the Home Assistant format `domain.name` (lowercase letters, digits,
  `_`); an invalid binding is skipped, the others keep running.
- No device/area data in the browser: use an administrator's token.
- An object shows quality *bad*: check the entity in Home Assistant (`unavailable`, removed)
  or the instance's connection.

## Webhook: an incoming HTTP call as a source {#adapters-webhook}

Many devices can only **call a URL** when something happens — no custom headers, no body, no
MQTT. A door station calls a configured URL when the bell is pressed, an IP push button when
a key is pressed, a camera on motion. The **WEBHOOK** adapter type turns such a call into a
value on an object. From there it behaves like any other source: an additional DEST binding
(KNX, for example) on the same object puts the telegram on the bus, which the chime, the Visu
and the Logic engine react to.

### Setting up an instance

| Field | Meaning |
|---|---|
| **Path prefix** | The path the instance is reachable under. Default `/hook`. At most three segments; `api`, `assets`, `help`, `setup` and `visu` are taken. |
| **Trust X-Forwarded-For** | Only enable this behind a reverse proxy. Without one, any caller could claim an allowed source address. |
| **Rate limit** | Accepted calls per minute and source IP. Beyond that the endpoint answers `429`. |

Two instances cannot claim the same prefix; the second reports an error and stays
disconnected.

### Creating a binding

A webhook binding is created like any other: pick the webhook instance under **Bindings** on
the object. The direction is always **read (SOURCE)** — a webhook is an entry point.

| Field | Meaning |
|---|---|
| **Slug** | The path segment of the call URL, e.g. `front-door-bell`. Lower-case letters, digits, `-` and `_`; unique per instance. |
| **Allowed HTTP methods** | `GET`, `POST` or both. Devices that can only call a URL use `GET`. |
| **Value source** | **Fixed value** for push buttons and doorbells (`true`), or **value from the request**. |
| **Parameter / field name** | For *value from the request*: the query parameter for `GET` (`?value=1`), or the field of the same name in the JSON body for `POST`. Default `value`. The name `token` is not allowed — that parameter carries the credential. |
| **Allowed networks (CIDR)** | Which caller addresses may trigger this slug — the fixed address of exactly this door station, for example. Entries are managed one row at a time. Empty = no restriction. |
| **Debounce (ms)** | Further calls within this window are acknowledged with `204` but do not set the value again. `0` = off. |
| **Auto-reset** | Optional: sets the datapoint back to the reset value after the configured time — see below. |

### Allowed networks

Which caller addresses may trigger a webhook is configured **per binding**, not
on the instance: it is a property of the one device behind that slug, not of the
endpoint as a whole. Each binding gets exactly its own device's address; an empty
list does not restrict anything. The instance keeps only what is genuinely
endpoint-wide — the path prefix, how the caller address is determined, and the
rate limit.

::: warning Careful behind a reverse proxy
With a reverse proxy (nginx, Caddy) in front of OBS, every call arrives with
the proxy's address — `127.0.0.1` for a proxy on the same host. The allowlist
then no longer does what it looks like it does. Enable **Trust X-Forwarded-For**
in that case, *and* make sure the proxy sets that header itself and overwrites
one sent by the client.
:::

The value is converted to the object's data type — `1`, `true`, `on` and `yes` become `true`
on a Boolean object, `0`, `false`, `off` and `no` become `false`. If the value does not fit
the data type, the endpoint answers `400` and nothing is set. The formula and value mapping
from the *Transformation* tab apply just as for any other source.

### Auto-reset: turning the webhook into a trigger

A doorbell reports an *event*, not a state. Left alone, the datapoint would stay
at `true` forever after the first ring, and the second press would produce no
edge at all — the chime, the Visu and the Logic engine would see nothing.

With **auto-reset** the adapter sends the **reset value** by itself after the
configured **reset delay**. One call then puts two values on the bus — `1`,
shortly after `0` — and the next press is a fresh edge again.

- Both values take the same path: type coercion, formula and value map apply to
  the reset value exactly as they do to the triggered one. A value map turning
  `true` into `on` therefore maps the reset `false` to `off`, not to something
  else.
- The reset reaches the DEST bindings of the same object like any other source
  value — which is what puts the `1`/`0` pair on KNX.
- **Retriggerable:** another call while the timer runs restarts it rather than
  adding a second one. Ringing twice keeps the value until the configured time
  after the *last* press. This applies only to calls that are accepted: a call
  inside the **Debounce** window is confirmed with `204` but ignored — it does
  not restart the timer. With a debounce window longer than the reset delay the
  pulse therefore always ends after the delay of the *first* accepted call.
- A delay of `0` resets immediately — a pure pulse.
- Changing the reset behaviour of the binding (or its value map, formula or
  object) or stopping the instance discards a pending reset. It belongs to the
  configuration it was armed under; rotating a token does not affect it.

The binding's direction stays **read (SOURCE)**: the adapter still only ever
feeds values *into* OBS and never writes out to a protocol endpoint. The reset
is one more value from that source — on its own clock rather than on a call, no
different in kind from a polling adapter reporting a second reading.

### Call URL and token

On creation the server issues **a separate token per binding**. After saving, the form shows
the ready-to-copy call URL in two variants:

```
http://obs:8080/hook/front-door-bell?token=<secret>
http://obs:8080/hook/front-door-bell/<secret>
```

The second variant helps with devices whose configuration field does not accept query
parameters. Both behave the same:

| Response | Meaning |
|---|---|
| `204` | Value accepted (or deliberately discarded by the debounce) |
| `400` | Value missing or incompatible with the object's data type |
| `404` | Unknown slug, wrong token, method not allowed, or blocked source address — deliberately indistinguishable |
| `429` | The instance's rate limit was exceeded |

::: tip The URL is built from the address the Admin GUI is currently open on
Administering OBS via `http://localhost:8080` yields a `localhost` URL to copy —
and a call from there arrives as `127.0.0.1`. If the allowlist only names the
LAN in the binding's allowlist (say `10.38.0.0/16`), that very test call is rejected with `404` even though
the slug and the token are correct. The form points this out as soon as the
allowlist does not cover the caller currently in use; to test, either add
`127.0.0.1` or open the GUI through the LAN address.
:::

Below the URL the form also shows this binding's **calls**, **values set** and **last call**.
These counters live in the running process and start at zero again after a restart; the
values themselves appear in the Monitor and the history as always, there with `WEBHOOK` as
the source.

Rejected calls appear next to them with their **reason and caller address** —
rate limit, address not in the allowlist, unknown slug, wrong token, or a
method that is not allowed. Because the outward answer stays
an indistinguishable `404` on purpose, this display is the only place a silent
failure can be recognised at all. Both are shown in a binding's form, below its call URL: that
binding's own counters first, then — in a separate box — the instance-wide total of turned-away calls. That total also
contains the calls the server could not match to any binding (an unknown slug, a wrong token) and
the rate limit, so it overlaps with the binding's own counters.

**Issue a new token** revokes the previous URL immediately and hands out a new one — when a
device is replaced, for instance, or when its configuration ended up in the wrong hands.
Other bindings and integrations are untouched.

::: warning The token is part of the URL — and therefore of logs
Both URL variants carry the token in clear text, as `?token=…` or as the last path
segment. OBS masks it as `[redacted]` in its own access log (the journal or
`docker logs`); the caller address, method, slug and status code stay visible for
troubleshooting. Everything **in front of** OBS still sees the unmodified URL and
often logs it: an upstream reverse proxy (nginx, Caddy, Traefik), the calling
device itself, and any log shipping that collects those logs.

- Restrict access to these logs and limit how long they are retained.
- If a token has been exposed in a log or a device configuration, use **Issue a new
  token** — the old URL stops working immediately.
:::

### Why a token per binding instead of an API key

An API key may write every object. In a device mounted outside the house, whose configuration
is often stored in clear text, that would be a real risk — all the more so because tokens end
up in device and proxy logs. A webhook token, by contrast, authorizes **exactly this one
object with exactly this value mapping**: a compromised device can only ring the bell.

For the same reason a webhook binding **cannot** point at an object of control class
`central_plant` — the same boundary the Visu's anonymous write path draws. If an object is
reclassified afterwards, the endpoint refuses the call with `403`.

Creating and rotating a binding, on the other hand, stays an ordinary configuration change:
it requires write permission (role *operator*) on the adapter instance and is only possible
with a user login, never with an API key.

## MESSAGE: Pushover options {#adapters-message-pushover}

When a MESSAGE binding or the **Notification** logic block has at least one Pushover target
selected, the **Pushover options** section appears. The options apply to the Pushover targets
only — Telegram, seven.io and other providers send unchanged. Empty fields are not sent to
Pushover.

| Option | Effect |
|---|---|
| **Image URL** | OBS downloads the image and sends it as an attachment (one image, max. 5 MB, JPEG/PNG/GIF/WebP). |
| **URL**, **URL title** | Additional clickable URL with its own title. A URL title without a URL is invalid; if a template renders an empty URL at runtime, the message is sent without a link. |
| **Priority** | `-2` lowest up to `2` emergency. **Default** equals the normal priority `0`. |
| **Retry**, **Expire** | Only for priority `2`, and then required: retry at least 30 seconds, expire at most 10800 seconds. |
| **Sound** | Overrides the target's default sound. Pick one of the Pushover sounds or type the name of a custom sound. |
| **Formatting** | Plain text, HTML or monospace — only one at a time. With HTML the template is HTML; inserted values are HTML-escaped for Pushover. |
| **TTL** | Lifetime in seconds; Pushover deletes the message afterwards (ignored for priority `2`). |
| **Timestamp** | A different message time as Unix time in seconds. |

In a binding, image URL, URL and URL title support the same placeholders as the message template
(`###DP###`, `###DPN###`, `###DPU###`, `###DPI###`, `###TS###`, `###DATE###`, `###TIME###`). In image URL and URL, OBS encodes characters a URL cannot carry directly (spaces, `+`, umlauts …); URL delimiters such as `/`, `?`, `&` and `#` are kept so that an object can also supply a complete URL. Values containing `&` or `#` therefore do not belong in a query parameter. In
the logic block, the connected **URL**, **URL title** and **Image URL** inputs override the
static values.

### Image sources and the URL Target Allowlist

- **External image sources** must use HTTPS, e.g. `https://camera.example/snapshot.jpg`.
- **Internal cameras** in private address ranges may use HTTP or HTTPS but must be released in
  the **URL Target Allowlist** under *Settings → Security* — as an IP address or network, e.g.
  `192.168.1.50/32` for `http://192.168.1.50/snapshot.jpg`. For a hostname the resolved IP
  address counts; an entry with only the hostname is not enough.
- Credentials in the image URL (`http://user:password@…`) are only sent once the camera asks for them — via Digest when it offers Digest, otherwise via Basic.
- The release only lifts the block on that target. OBS still connects only to the checked IP
  address (DNS rebinding protection), re-checks every redirect, verifies content type, image
  content and size, and aborts after fixed timeouts.

If the image cannot be loaded safely, delivery to that target counts as failed: the message is
not sent without the image, the error appears in the log, the adapter status and on the logic
block, and its **Sent** output does not fire. Credentials from the image URL, the API token and
the user key never appear in error messages.

## Scheduling {#adapters-zeitschaltuhr}

The scheduling adapter is a pure **source**: it writes to objects at defined points in time
but never reads from them. One binding is exactly **one schedule point** — for several
switching times on the same object, create several bindings.

| Schedule type | Switches |
|---|---|
| Daily | every day, or on selected weekdays |
| Annual | in selected months (no selection = all), optionally on a fixed day of the month |
| Holiday | on selected holidays (no selection = all holidays) |
| Metadata | not a schedule point — publishes the holiday/vacation status automatically |

The switching time is either a fixed time of day or tied to the position of the sun
(sunrise, sunset, solar noon, or a solar altitude angle), each with an offset in minutes.
A schedule point can also repeat on a cadence — hourly at the given minute, or every minute.
Holidays and vacations can be ignored, skipped, switched exclusively, or treated like a
Sunday, per schedule point.

### Output value {#adapters-zeitschaltuhr-value}

The **output value** is parsed against the type of the bound object — so a schedule can
drive any object type, not just on/off:

| Object type | Input control | Accepted values |
|---|---|---|
| Yes/No | On/Off select | `1`/`0`, `true`/`false`, `on`/`off`, `ein`/`aus` |
| Whole number | Number field | whole number, e.g. `50` |
| Decimal number | Number field with unit | decimal number, e.g. `21.5` |
| Text | Text field | taken literally — including `1`, `0`, `on` or `ein` |
| Date | Date picker | ISO 8601, e.g. `2026-12-24` |
| Time | Time picker | ISO 8601, e.g. `08:00:00` |
| Timestamp | Date/time picker | ISO 8601, e.g. `2026-12-24T08:00:00` |
| Unknown | Text field | heuristic: yes/no literal → whole number → decimal number → text |

The input control therefore follows the object: a blind position object of type decimal
number gets a number field with its unit, a yes/no object gets an On/Off select.

If the value does not fit the object type, the error appears directly below the field and
**saving is rejected**. The mistake surfaces while the schedule point is being created
rather than hours later when it fires. If a value that was already stored still cannot be
converted at switching time — because the object type was changed afterwards, say — OBS logs
a warning, records a `type_mismatch` diagnostic on the object, and skips the switch.

Metadata bindings have no output value: they publish the holiday/vacation status themselves.
