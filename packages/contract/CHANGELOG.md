# Changelog — `@obs/visu-contract`

Alle nennenswerten Änderungen am Vertrag werden hier dokumentiert. Der Vertrag koppelt
die obs-Visu-App und die Skins ausschließlich über **Daten (JSON) und Typen** — er führt
nichts aus (Goldene Regel 7).

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/); die Versionierung
folgt der unten dokumentierten Semver-Policy (CONTRACT-v1.md §9).

## Semver-Policy (§9)

Eine Versionsänderung beschreibt immer einen Diff an der **Datenform** oder der **Typen-/
Aktions-Oberfläche**. Jeder Bump steht hier mit den neuen/geänderten Typen.

- **Patch (1.0.x):** Fixtures ergänzt, Doku — **keine** Formänderung. Bestehende Skins und
  die App bleiben unverändert gültig.
- **Minor (1.x):** neuer Widget-Typ **oder** neue *optionale* Felder/Aktionen. Bestehende
  Skins bleiben gültig; für neue Typen erscheinen sie in ihrer Fixture-Wand als `gap`, bis
  sie nachziehen oder den Typ bewusst als `unsupported` deklarieren.
- **Major (x):** Bruch an Datenform oder Aktion (entferntes/umbenanntes/typ-geändertes Feld,
  geänderte Aktionssemantik). Skins **müssen** ihr `targetsContract` anheben.

> Die Fixture-Wand eines Skin-Autors wird an genau den geänderten Stellen rot — das ist
> gewollt: ein Formbruch ist sichtbar, kein stiller Default (Goldene Regeln 2 + 3).

## [1.0.0] — 2026-06-09

Erste stabile Vertragsversion (`version: "1.0"`).

### Added

- **Globals (§2):** `roles` `[compact, default, wide, tall, feature, banner]` und die
  semantischen `iconSlots` (Default-Set aus `reference/vue-ionic/store.js → ICONS`).
- **Stabiler Kern v1 (§3):** Widget-Typen `light`, `switch`, `blind`, `jalousie`, `sensor`,
  `scene` — je mit `data`, `actions`, `icon`, `roles` und einem maschinell validierbaren
  `dataSchema`. Jalousie-Semantik: `position` 0=auf/100=zu, `slat` 0–100 ⇒ 0–90°, `locked`,
  `statuses[]` (`true|false|null`).
- **Reserved für v1.1 (§3):** `climate`, `weather`, `energy`, `chart`, `media`, `camera`,
  `alarm` im Schema deklariert (`reserved: true`), damit Skins sie bewusst abwählen
  (`unsupported`) können und der Generator sie nicht als `gap` fehlinterpretiert.
- **Fixtures (§4):** Musterzustände je Kern-Typ — `light: off/on/dimmed`, `switch: off/on`,
  `blind: open/half/locked`, `jalousie: open/tilted/locked`, `sensor: ok/warn`,
  `scene: film/morgen` — mit `contractVersion: "1.0"`. Accents sind Palette-Schlüssel,
  nie Hex.
- **Typen (§5/§7/§8):** `Device`-Union (schreibgeschützt) inkl. `LightDevice`,
  `SwitchDevice`, `BlindDevice`, `JalousieDevice`, `SensorDevice`, `SceneDevice`; `Tokens`;
  `Ctx` (`stateText`, `hyphenate`, `icon`, `nf`, `warn`) als Sandbox-Grenze; `Renderer`
  (`(d, t, ctx) => string | VNode`); `SkinManifest`; `SupportReport`.
- **Exports:** `index.ts` exportiert `schema`, `fixtures`, `version` (= `"1.0"`) und die
  Typen.

[1.0.0]: https://github.com/Micsi/openbridgeserver/tree/feat/visu-mobile-skins/packages/contract
