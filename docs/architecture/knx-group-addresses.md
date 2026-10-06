# KNX group addresses

How OBS writes, stores and compares KNX group addresses, and where the project's address style
comes from. Applies to everything that reads, stores, keys or compares a group address text: the
KNX adapter, the `.knxproj` import, the binding API and the KNX read endpoints.

## Why

ETS knows three notations for the same 16-bit group address:

| Style (`GroupAddressStyle`) | Notation | Example for raw 2282 |
|---|---|---|
| `ThreeLevel` | main / middle / sub | `1/0/234` |
| `TwoLevel` | main / sub | `1/234` |
| `Free` | raw number | `2282` |

Before #1296 OBS passed the text through in whatever notation it arrived. xknx formats telegram
addresses three-level, so a binding imported from a two-level or free project was keyed with
`1/234` or `2282` and looked up with `1/0/234` — incoming telegrams never reached the datapoint,
without any error.

## Rules

1. **One internal notation.** Inside OBS a group address is the three-level text `main/middle/sub`
   without leading zeros or whitespace (`1/0/234`). It is what xknx formats by default and what the
   ring buffer history already contains, so three-level installations do not change.
2. **Normalize at every entrance.** Text from outside OBS passes through `normalize_ga()` before
   anything else happens to it:
   - the `.knxproj` import — group addresses, communication object ↔ GA links, function ↔ GA links,
     and the bindings the import creates (`obs/knxproj/parser.py`, `obs/api/v1/knxproj.py`);
   - saving a KNX binding through the API — `group_address` and `state_group_address`
     (`obs/api/v1/bindings.py`); `KnxBindingConfig` normalizes both fields itself, so every
     consumer of the model gets internal addresses;
   - group address path and query parameters of the KNX endpoints;
   - the telegram side in the adapter: `normalize_ga(str(telegram.destination_address))`.
     `str()` of an xknx `GroupAddress` depends on the process-wide `GroupAddress.address_format`.
3. **Store, compare and key only internal addresses.** Dictionary keys, set members, `==`/`in`
   comparisons and SQL comparisons work on normalized text. A raw binding config read from the
   database (which may predate #1296) is compared through `try_normalize_ga()`.
4. **Display only through `format_ga(address, style)`.** It renders an internal address in the
   project's style; a two-level project keeps seeing and typing `1/234`.
5. **Invalid input is an error, not a silent skip.** `normalize_ga()` raises `InvalidGroupAddress`
   (a `ValueError`, so Pydantic turns it into a 422). `try_normalize_ga()` returns `None` and is only
   for tolerant readers of already-stored data, where one broken row must not abort the operation.

## The module

`obs/adapters/knx/group_address.py` holds `normalize_ga()`, `try_normalize_ga()`, `format_ga()`
and the style constants. It sits next to `dpt_registry.py`, the other piece of KNX knowledge shared
by the adapter and the import. It is pure Python on purpose: the result must not depend on xknx's
process-wide formatting setting, and the import path can use it without importing the adapter.
There is exactly one implementation; a guardrail test fails on a second definition.

## Where the style comes from

The `.knxproj` import reads xknxproject's `info.group_address_style` (`ThreeLevel`, `TwoLevel` or
`Free`, taken from the `GroupAddressStyle` attribute in `project.xml`) and stores it in the
single-row table `knx_project` (not in `app_settings`: every `app_settings` row ends up in the Logic
engine's application config). Migration V55 creates and seeds it for existing installations:
they stored addresses in the project's own notation, so a uniform part count reveals the style
(3 → `ThreeLevel`, 2 → `TwoLevel`, 1 → `Free`); empty or mixed data falls back to `ThreeLevel`.

`GET /api/v1/knxproj/group-addresses` returns the style as `group_address_style` next to the
internal addresses, and its search additionally matches an address typed exactly in the project's
notation. The import result carries the style as well.

## Guardrail

`tests/unit/test_knx_group_address_architecture.py` scans `obs/` and fails when a raw group address
text is used as a key or in a comparison without passing a normalizer:

- **Sources** (raw text): `str(<x>.destination_address)`; `<dict>.get("group_address" |
  "state_group_address")` and the subscript form; FastAPI route parameters named `ga`,
  `group_address` or `state_group_address`; and local names assigned from these (also through
  `str()`, `.strip()`, `.lower()`, `.upper()`, `or` and tuple, list or set displays).
- **Sinks** (key or comparison): `==`, `!=`, `in`, `not in`; subscript indices; dict keys; set
  comprehension elements; the first argument of `get`, `setdefault`, `pop`, `add`, `discard`,
  `remove`; and arguments passed one call deep, within the same module, to a parameter that reaches
  one of these.
- **Models:** every Pydantic field named `group_address` or `state_group_address` must have a
  `field_validator` calling `normalize_ga()`.

Deliberately **not** detected: flows through containers and loop variables, across modules or
deeper than one call; storing raw text (SQL parameters, records); comparisons inside SQL. These are
covered by the behavioural tests — `tests/adapters/test_knx_group_address_styles.py` (telegram in,
datapoint value out, per style) and `tests/integration/test_knxproj_group_address_styles.py`
(import per style, read endpoints).

## Adding code that handles group addresses

- Reading a group address from outside (request, file, telegram, stored binding config)? Normalize it
  right where it is read.
- Building a lookup table or comparing addresses? Use normalized text on both sides.
- Showing an address to a user? `format_ga(address, style)` with the stored style.
- Test it in all three styles; the fixtures in `tests/knxproj_style_variants.py` derive two-level and
  free projects from the demo project at test time.
