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
- **Import** (ioBroker only) — imports ioBroker states as new OBS objects with a binding.
- **Manage objects** (presence simulation only) — selects simulated Boolean/Integer objects
  and manages their bindings.
- **Migrate bindings** — moves all of this instance's bindings to another instance of the
  same adapter type; bindings already present at the target are skipped.
- **Delete instance** — deletes the instance irreversibly, including all of its bindings.

"Enabled" turns the instance off entirely without deleting it — a disabled instance keeps
its configuration and bindings but does not connect.

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
