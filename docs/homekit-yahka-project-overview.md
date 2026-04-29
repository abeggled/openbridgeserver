# HomeKit/Yahka Integration for OBS

## Why this project exists

Many installations already use OBS as the place where rooms, widgets,
datapoints, KNX bindings, and automation logic come together. At the same time,
Apple Home is often reached through ioBroker and Yahka because they already
provide a practical HomeKit bridge.

Without an OBS-aware integration workflow, Apple Home onboarding tends to be
manual and error-prone:

- HomeKit state IDs are created by hand
- existing OBS and KNX datapoints are duplicated instead of reused
- write and status paths drift apart
- naming, room structure, and behavior become inconsistent across systems
- fallback and restore behavior are hard to reason about later

This project adds a structured migration/helper layer to OBS so existing VISU
and adapter models can be translated into stable HomeKit-facing ioBroker states
and bindings in a predictable way.

## Core idea

The core idea is simple:

`OBS stays the source of structure. ioBroker/Yahka stays the HomeKit bridge.`

OBS already knows the installation's rooms, widgets, datapoints, and protocol
bindings. That means OBS can generate a reviewable HomeKit plan from the VISU
tree instead of asking the user to remodel the same installation a second time
inside Yahka.

The integration therefore does not try to replace Yahka. It helps prepare and
maintain the data model that Yahka needs:

- stable ioBroker state IDs
- predictable room and accessory naming
- explicit read/write direction per state
- reuse of existing KNX/ETS datapoints where possible
- clear detection of unsupported or risky cases before productive changes happen

## What OBS adds

The new OBS-side feature set is an experimental migration helper with two main
steps.

### 1. Preview

OBS can analyze the existing VISU tree and generate a reviewable HomeKit/Yahka
mapping preview.

The preview shows:

- rooms and accessories derived from the VISU hierarchy
- normalized ioBroker state IDs
- the intended HomeKit service type
- binding direction per state
- KNX status/write datapoint metadata where available
- warnings for unsupported widgets or risky mappings
- estimated accessory count and bridge-limit pressure

The preview is intentionally read-only.

### 2. Controlled apply

After review, OBS can execute a controlled apply step.

This apply logic can:

- create missing OBS datapoints when truly needed
- reuse existing KNX/ETS datapoints instead of creating parallel HomeKit objects
- create ioBroker bindings for the generated HomeKit-facing state IDs
- optionally create the corresponding ioBroker states through the native
  ioBroker adapter

The apply flow is dry-run first by default and designed to be idempotent where
possible.

## Why this is useful beyond one installation

Although the original driver was a concrete migration project, the value is not
site-specific. The useful general part for OBS is:

- using the VISU model as a source for external integration planning
- generating stable, reviewable state namespaces from widgets and rooms
- attaching external integration bindings to existing datapoints instead of
  duplicating the object model
- making risky integration work previewable before writes occur
- codifying read/write semantics for external bridges such as HomeKit

That pattern can be useful anywhere OBS acts as the system-of-record for the
building model and another system acts as the end-user presentation bridge.

## Current scope

The current preview/apply flow targets these widget families:

- `Licht` -> `Lightbulb`
- `Toggle` -> `Switch` or `Outlet`
- `Fenster` -> `ContactSensor`
- `Rolladen` -> `WindowCovering`
- `RTR` -> `Thermostat`
- `ValueDisplay` -> temperature or humidity sensor when detectable

Unsupported widgets are not silently ignored. They are surfaced as review items
so the user can decide what should happen before a productive rollout.

## Design principles

Several design principles matter more than any single endpoint:

- Reuse before create:
  Existing KNX/ETS-backed OBS datapoints should remain the leading objects
  whenever they already represent the target function.

- Preview before write:
  The user should be able to inspect the exact mapping and apply plan before any
  productive change is made.

- Explicit direction:
  Writable HomeKit states and read-only status states must not be treated the
  same. The integration needs a clear semantic distinction between
  bidirectional states and status-only states.

- Stable naming:
  HomeKit-facing ioBroker state IDs must stay predictable and durable over time.

- Upstream-safe defaults:
  Core code and docs should stay neutral and not depend on one installation's
  hostnames, IPs, bridge identities, or room names.

## Relationship to the native ioBroker adapter

This project builds on top of the native ioBroker adapter work in OBS.

That adapter already provides the technical foundation needed for a generic
HomeKit/Yahka workflow:

- connect to one or more ioBroker instances
- browse ioBroker states
- subscribe to live state changes
- write back to ioBroker states
- create ioBroker states programmatically
- recover from reconnect/resubscribe scenarios more reliably

The HomeKit/Yahka helper is therefore best understood as an adapter-oriented
workflow, not as a separate HomeKit runtime inside OBS.

## Intended GUI direction

The most natural GUI placement is inside the adapter section, scoped to a
selected ioBroker instance:

`Adapter -> ioBroker -> HomeKit/Yahka`

That keeps the workflow close to the adapter instance that will receive the
generated bindings and optional state creation. It also avoids turning the OBS
GUI into a full Yahka management tool.

The most useful GUI stages are:

- Preview
- Plan / dry-run
- Apply

This is enough to make the workflow understandable and safe without duplicating
all Yahka-specific administration in OBS.

## What this project is not

This project does not aim to:

- replace Yahka as the HomeKit bridge
- manage HomeKit pairing/HAP identities directly in OBS
- become a full Apple Home configuration editor
- hide fallback, restore, or bridge-limit concerns behind magic defaults

Those concerns remain important, but they should stay explicit.

## Practical outcome

In practical terms, this project gives OBS a new role:

OBS becomes the place where an existing installation can be translated into a
structured, reviewable, and partially automatable HomeKit/Yahka rollout instead
of forcing users to rebuild that structure manually in parallel systems.

That is valuable for:

- first-time Apple Home onboarding
- controlled pilot migrations room by room
- avoiding datapoint duplication
- documenting integration intent
- future maintenance, restore, and troubleshooting work

## Summary

The kernel of the concept is not "add HomeKit to OBS directly."

The kernel is:

- use OBS as the source of truth for structure
- use ioBroker/Yahka as the HomeKit bridge
- let OBS generate and apply a safe, reviewable integration plan
- preserve the existing datapoint model instead of fragmenting it

That makes the feature useful not only for one local migration, but as a
general integration pattern for OBS installations that want a structured path
into Apple Home.
