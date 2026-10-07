# KNX group addresses

How OBS writes, stores and compares KNX group addresses, where the project's address style comes
from, and how the rules are enforced. Applies to everything that reads, stores, keys or compares a
group address text: the KNX adapter, the `.knxproj` import, bindings, the config import, the
ringbuffer metadata and the KNX endpoints.

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
   without leading zeros or whitespace (`1/0/234`). It is what xknx formats by default, so
   three-level installations do not change.
2. **Normalize at every entrance.** Text from outside passes through `normalize_ga()` before it is
   stored, compared or used as a key:
   - the `.knxproj` import — group addresses, communication object ↔ GA links, function ↔ GA links
     and the bindings it creates (`obs/knxproj/parser.py`, `obs/api/v1/knxproj.py`);
   - saving a KNX binding through the API and the JSON config import — `group_address` and
     `state_group_address` (`obs/api/v1/bindings.py`, `obs/api/v1/config.py`), and the group
     addresses of the config import;
   - group address path and query parameters of the KNX endpoints, and the ringbuffer's group
     address filter (`obs/ringbuffer/ringbuffer.py`);
   - the telegram side in the adapter: `normalize_ga(str(telegram.destination_address))`, because
     `str()` of an xknx `GroupAddress` depends on the process-wide `GroupAddress.address_format`.
   The CSV group-address import, which only accepted three-level rows, was removed instead.
3. **Store, compare and key only internal addresses.** The `knx_*` tables, KNX binding configs and
   new ringbuffer metadata hold internal texts; dictionary keys, `==`/`in` and SQL comparisons work
   on them. Readers of binding configs (adapter, traceability, ringbuffer snapshot, import upsert)
   still go through `try_normalize_ga()`, so a binding stored by another tool cannot break them.
   The datapoint copy reuses the stored, already internal configs.
4. **Existing data is migrated, not re-imported.** Migration V56 rewrites `knx_group_addresses`
   (primary key), `knx_co_ga_links` (foreign key with `ON DELETE CASCADE`), `knx_function_ga_links`
   and the KNX binding configs in one transaction. Several spellings of the same address are merged
   into one row: an existing internal row wins, otherwise the spelling in the project's notation
   (style from V55), then the others in sorted order. Empty fields are filled from every spelling;
   conflicting values are not dropped silently: they are logged and recorded in
   `knx_ga_merge_conflicts` (address, spelling, field, kept and dropped value), which
   `GET /api/v1/knxproj/group-addresses` returns as `merge_conflicts` (admins). Stored fields,
   descriptions included, are never rewritten with system text. The internal parent row is inserted
   before its links move and the raw one deleted afterwards, so no foreign key is ever violated.
   Texts that are no group address are left untouched. The migration is idempotent; a re-import
   afterwards finds the existing rows and bindings and creates no duplicates.
   Ringbuffer history is not rewritten: entries recorded before the update keep the binding text
   in the project's notation, and the group address filter expands every value to all three
   notations so it finds old and new entries alike.
5. **Invalid input is rejected or reported, never dropped silently.** `normalize_ga()` raises
   `InvalidGroupAddress` (a `ValueError`); the binding API and the config import answer with 422 or
   an error entry. For data that is already stored, the adapter treats an invalid feedback address
   (`state_group_address`) as absent — the binding keeps writing to its valid command address —
   skips a binding with an invalid command address, and reports both on the adapter card
   (status code `knxInvalidGroupAddresses`). The card status is composed from the connection
   status and this hint, never by overwriting: the hint is shown only while the adapter is connected
   and the connection status is less severe than a warning, so "disconnected", a connection error
   or the connection's own warning (tunnel pool) stays visible, and the hint returns as soon as it
   clears. The connected flag always comes from
   the connection status.
   `try_normalize_ga()` returns `None` and is only for such tolerant readers.
6. **Display goes through `format_ga(address, style)`** — not implemented in the Admin GUI yet.
   The API delivers internal addresses plus the project's `group_address_style`; the GUI still
   shows the internal notation. The group-address search already accepts the project's notation.

## The module

`obs/adapters/knx/group_address.py` holds `normalize_ga()`, `try_normalize_ga()`, `format_ga()`,
the style constants and `sql_is_internal_ga()`, the SQL form of "is internal" used by the database
triggers. It sits next to `dpt_registry.py`, the other piece of KNX knowledge shared by the adapter
and the import. It is pure Python on purpose: the result must not depend on xknx's process-wide
formatting setting, and the import path can use it without importing the adapter. A guardrail test
fails when another module defines a function with one of these names; a second implementation under
another name is not detectable that way and is left to review.

## Where the style comes from

The `.knxproj` import reads xknxproject's `info.group_address_style` (`ThreeLevel`, `TwoLevel` or
`Free`, taken from the `GroupAddressStyle` attribute in `project.xml`) and stores it in the
single-row table `knx_project` (not in `app_settings`: every `app_settings` row ends up in the Logic
engine's application config). Migration V55 creates the table; for existing installations the
part count of the stored valid addresses reveals the style by majority (3 → `ThreeLevel`,
2 → `TwoLevel`, 1 → `Free`); rows that are no group address do not count, no valid rows or a tie
fall back to `ThreeLevel`. V55 runs before V56 rewrites the
addresses. The factory reset clears it; the JSON config export does not carry it (a restored
instance shows `ThreeLevel` until the next import).

`GET /api/v1/knxproj/group-addresses` returns the style as `group_address_style` next to the
internal addresses, and its search additionally matches an address typed exactly in the project's
notation. The import result carries the style as well.

## Enforcement

Three layers, from strongest to weakest:

1. **Database triggers (V56).** `BEFORE INSERT` and `BEFORE UPDATE` triggers on
   `knx_group_addresses.address`, `knx_co_ga_links.ga_address` and `knx_function_ga_links.ga_address`
   abort any write of a non-internal text, whatever code issued it. Plain SQL (`GLOB`), so tools that
   open the database without OBS keep working; a test checks the predicate against `normalize_ga()`
   for all 65536 addresses. Binding configs are JSON, may still hold legacy invalid addresses and are
   rewritten by unrelated edits, so they are not trigger-protected.
2. **Data invariant** (`tests/knx_group_address_invariant.py`). Integration and upgrade tests drive
   every entrance with two-level and free inputs and then scan every storage place for non-internal
   texts: the three `knx_*` columns, both GA fields of every KNX binding, and the binding snapshot of
   new ringbuffer entries (it also feeds `ringbuffer_metadata_bindings.group_address`). The list was
   taken from all schemas and every persisted JSON document; filter sets store device PAs, logic
   graphs, Visu nodes and settings hold no group addresses. A new storage place must be added there,
   otherwise the invariant does not see it. Separate tests prove that the triggers of layer 1 abort
   raw inserts and updates on all three columns.
3. **AST guardrail** (`tests/unit/test_knx_group_address_architecture.py`), limited to patterns it
   recognizes reliably: `str(<x>.destination_address)`, `.get`/`[...]` of `group_address` or
   `state_group_address`, and GA route parameters, used as a key or in a comparison (also one call
   deep within a module and through list comprehensions); comparisons with literals such as `== ""`
   are ignored. It also requires every Pydantic field named `group_address` or
   `state_group_address` to have a `field_validator` calling `normalize_ga()`/`try_normalize_ga()`.
   It does not see SQL, storage, flows across modules or other field names; layers 1 and 2 do.

Behavioural tests per style: `tests/unit/test_knx_group_address.py` (the module),
`tests/adapters/test_knx_group_address_styles.py` (telegram in, datapoint value out),
`tests/integration/test_knxproj_group_address_styles.py` (import, read endpoints, traceability,
config import, ringbuffer filter, data invariant) and `tests/unit/test_knx_group_address_upgrade.py`
(an installation created with the pre-#1296 code, upgraded; fixtures from
`tools/knx_legacy_fixture.py`).

## Adding code that handles group addresses

- Reading a group address from outside (request, file, telegram, foreign binding config)? Normalize
  it right where it is read.
- Storing one in a new column? Store the internal text and add the column to the data invariant;
  for a plain text column also add the triggers.
- Building a lookup table or comparing addresses? Use normalized text on both sides.
- Showing an address to a user? `format_ga(address, style)` with the stored style.
- Test it in all three styles; `tests/knxproj_style_variants.py` derives two-level and free projects
  from the demo project at test time.
