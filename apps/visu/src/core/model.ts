/**
 * core/model — the device/room model of the obs Visu mobile app.
 *
 * Ported 1:1 in shape from reference/vue-ionic/store.js (the `list` device
 * dataset and the `mobileGroups` room grouping). Data shapes follow
 * CONTRACT-v1 §3 — every device here is a contract-v1 *core* `Device`
 * (light | switch | blind | jalousie). The reserved tablet/desktop widget
 * types (climate, weather, energy, chart, media, camera, alarm) are out of
 * scope for the mobile model and intentionally not ported.
 *
 * Goldene Regeln honoured here:
 *  - One model/state lives in core; this module is that single source of
 *    truth for the mobile device list (Regel 1).
 *  - "Renderer rein": this file imports NO skin and NO renderer — only the
 *    data/type contract `@obs/visu-contract`.
 *  - The model is read-only to the outside: `devices`, `byId`, `rooms` are
 *    frozen and typed `readonly`. Canonical mutations live in the host
 *    action layer (CONTRACT-v1 §6), not here.
 *
 * Data and behaviour are kept apart (Regel "Daten=JSON, Verhalten=Code"):
 * the device shapes mirror the contract data, the only code here is the pure
 * `layoutRole` mapping.
 */

import type {
  Device,
  LightDevice,
  SwitchDevice,
  BlindDevice,
  JalousieDevice,
  Role,
} from '@obs/visu-contract';

/* ------------------------------------------------------------------ helpers */
// Small constructors mirroring store.js (L/SW/B/J) so the dataset below reads
// the same way the prototype did. They only assemble plain data — no logic.

function light(
  id: string,
  room: string,
  label: LightDevice['label'],
  accent: LightDevice['accent'],
  extra: Partial<Pick<LightDevice, 'on' | 'dim'>> = {},
): LightDevice {
  return { id, type: 'light', room, label, accent, on: false, dim: null, ...extra };
}

function swtch(
  id: string,
  room: string,
  label: SwitchDevice['label'],
  accent: SwitchDevice['accent'],
  extra: Partial<Pick<SwitchDevice, 'on'>> = {},
): SwitchDevice {
  return { id, type: 'switch', room, label, accent, on: false, ...extra };
}

function blind(
  id: string,
  room: string,
  label: BlindDevice['label'],
  accent: BlindDevice['accent'],
  extra: Partial<Pick<BlindDevice, 'position' | 'locked'>> = {},
): BlindDevice {
  return { id, type: 'blind', room, label, accent, position: 0, locked: false, ...extra };
}

function jalousie(
  id: string,
  room: string,
  label: JalousieDevice['label'],
  accent: JalousieDevice['accent'],
  extra: Partial<
    Pick<JalousieDevice, 'position' | 'slat' | 'locked' | 'invert' | 'moving' | 'statuses'>
  > = {},
): JalousieDevice {
  return {
    id,
    type: 'jalousie',
    mode: 'jalousie',
    room,
    label,
    accent,
    position: 0,
    slat: 0,
    locked: false,
    invert: false,
    moving: null,
    statuses: [],
    ...extra,
  };
}

/* ------------------------------------------------------- device dataset §3 */
// The mobile-overview devices, in store.js source order. Each device carries
// its own state; screens reference them by id.

const list: readonly Device[] = [
  // ── Küche ──
  light('kueche-wand', 'EG Küche', 'Wandleuchten', 'orange'),
  light('kueche-pendel', 'EG Küche', 'Pendelleuchten', 'orange', { dim: 0 }),
  light('kueche-arbeit', 'EG Küche', 'Arbeitslicht', 'orange', { dim: 0 }),
  blind('kueche-roll', 'EG Küche', 'Rollladen', 'orange'),

  // ── WC & Bad ──
  light('wc-spiegel', 'EG WC', 'Spiegellicht', 'teal', { on: true }),
  swtch('wc-luefter', 'EG WC', 'Lüfter (10 Min)', 'teal', { on: true }),
  light('bad-spiegel', 'EG Bad', 'Spiegellicht', 'violet'),

  // ── Wintergarten ──
  light('wiga-pendel', 'Wintergarten', 'Pendelleuchten', 'green', { dim: 0 }),
  light('wiga-wand', 'Wintergarten', 'Wandleuchten', 'green', { dim: 0 }),
  blind('wiga-roll', 'Wintergarten', 'Rollladen', 'green', { locked: true }),
  jalousie('wiga-jalousie', 'Wintergarten', 'Jalousie Süd', 'green', {
    position: 62,
    slat: 35,
    statuses: [
      { label: 'Sturm', val: false },
      { label: 'Sonne', val: true },
      { label: 'Sperre', val: null },
    ],
  }),
  jalousie('wiga-jalousie-2', 'Wintergarten', 'Jalousie Ost', 'green', {
    position: 40,
    slat: 60,
    statuses: [
      { label: 'Sturm', val: false },
      { label: 'Sonne', val: true },
    ],
  }),

  // ── Schlafzimmer ──
  blind('schlaf-ost', 'EG Schlafz.', 'Rollladen Ost', 'orange'),
  blind('schlaf-sued', 'EG Schlafz.', 'Rollladen Süd', 'orange'),

  // ── Wohnzimmer ──
  blind('wohn-west', 'EG Wohnz.', 'Rollladen West', 'orange'),
  blind('wohn-balkon', 'EG Wohnz.', 'Rolladen Balkon', 'orange'),
  blind('wohn-sued', 'EG Wohnz.', 'Rollladen Süd', 'orange'),

  // ── Gäste & Treppe ──
  blind('gaeste-roll', 'EG Gästez.', 'Rollladen', 'orange'),
  light('treppe-eingang', 'EG Treppe', 'Hauseingang', 'orange'),
  light('treppe-haus', 'EG Treppe', 'Treppenhaus', 'orange'),
];

/** All mobile devices, in source order. Read-only to the outside (Regel 1). */
export const devices: readonly Device[] = Object.freeze(list);

/** Lookup by id — the canonical handle screens use to reference a device. */
export const byId: Readonly<Record<string, Device>> = Object.freeze(
  Object.fromEntries(list.map((d) => [d.id, d])) as Record<string, Device>,
);

/* ---------------------------------------------------------- room grouping */
// store.js → mobileGroups: each block is one room. Screens render them as
// separate grids with a gap between, so the spacing itself reads as
// "you are now in another room". Order + grouping are the floor (the layout
// baseline a skin may refine but not discard).

/**
 * One layout entry: the device id plus optional prominence hints carried over
 * from store.js. `span`/`row` are *hints*, not pixels — the page speaks roles
 * (CONTRACT-v1 §2), and {@link layoutRole} derives the contract role from them.
 */
export interface LayoutEntry {
  readonly id: string;
  /** Column span hint (mobile grid is 3-wide); 2 ⇒ a wider tile. */
  readonly span?: number;
  /** Row-span hint for tall tiles (e.g. the jalousie). */
  readonly row?: number;
}

/** One room block: an ordered list of layout entries. */
export interface RoomGroup {
  readonly room: string;
  readonly entries: readonly LayoutEntry[];
}

const e = (id: string, span?: number, row?: number): LayoutEntry =>
  span === undefined && row === undefined ? { id } : { id, span, row };

/** Ordered room blocks for the mobile overview (store.js → mobileGroups). */
export const rooms: readonly RoomGroup[] = Object.freeze([
  { room: 'Küche', entries: ['kueche-wand', 'kueche-pendel', 'kueche-arbeit', 'kueche-roll'].map((id) => e(id)) },
  { room: 'WC & Bad', entries: ['wc-spiegel', 'wc-luefter', 'bad-spiegel'].map((id) => e(id)) },
  {
    room: 'Wintergarten',
    entries: [
      e('wiga-pendel', 2),
      e('wiga-wand'),
      e('wiga-roll'),
      e('wiga-jalousie', 2, 3),
      e('wiga-jalousie-2', 2, 2),
    ],
  },
  { room: 'Schlafzimmer', entries: ['schlaf-ost', 'schlaf-sued'].map((id) => e(id)) },
  { room: 'Wohnzimmer', entries: ['wohn-west', 'wohn-balkon', 'wohn-sued'].map((id) => e(id)) },
  { room: 'Gäste & Treppe', entries: ['gaeste-roll', 'treppe-eingang', 'treppe-haus'].map((id) => e(id)) },
] satisfies RoomGroup[]);

/* ----------------------------------------------------------- span/row → role */
// CONTRACT-v1 §2: the page speaks ROLES (prominence), not pixels. The store.js
// layout hints map onto contract roles as follows:
//
//   • no hint        → the device type's contract default role
//                      (light/switch/blind: "default"; jalousie: "wide")
//   • span ≥ 2       → "wide"  (a tile that claims a wider cell)
//
// The jalousie keeps its contract default role ("wide") regardless of the
// extra row hint — its tallness is a render concern of the jalousie component,
// not a different prominence. A skin may refine these within the type's
// allowed roles; this mapping is only the baseline ("Reihenfolge + Gruppierung
// als Boden").

/** The contract default role per core widget type (CONTRACT-v1 §3 `roles.default`). */
const DEFAULT_ROLE: Record<Device['type'], Role> = {
  light: 'default',
  switch: 'compact',
  blind: 'default',
  jalousie: 'wide',
  sensor: 'compact',
  scene: 'default',
};

/**
 * Derive the contract prominence role for a layout entry + its device.
 * Pure function — no state, no side effects.
 */
export function layoutRole(entry: LayoutEntry, device: Device): Role {
  if (device.type === 'jalousie') return DEFAULT_ROLE.jalousie;
  if (entry.span !== undefined && entry.span >= 2) return 'wide';
  return DEFAULT_ROLE[device.type];
}
