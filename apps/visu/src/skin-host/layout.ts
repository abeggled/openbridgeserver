/**
 * skin-host/layout — the generic layout profile (A4, Issue #100).
 *
 * The page speaks order + grouping + roles (CONTRACT-v1.md §2/§4); the *skin*
 * declares HOW those map to space via `manifest.layout` (model `grid` | `list`,
 * a `roleMap`, and a `honors` list). This module is the host-side, skin-agnostic
 * translator: it consumes a manifest's layout block and produces a flat, ordered
 * list of placed items the renderer loop walks.
 *
 * The floor (Goldene Regel 5, ARCHITECTURE.md §4): **order + grouping are
 * mandatory**, role/span are **additive and ignorable**. So:
 *
 *   - Order is always preserved (rooms in source order, entries in source order).
 *   - Grouping is always preserved as group boundaries on each placed item.
 *   - A skin that honours "role" gets a grid span per item (from its `roleMap`);
 *     a skin that does not — or a `list` model — degrades to a flat 1×1 ordered
 *     list. Never broken, never reordered.
 *
 * The role itself comes from the core (`model.layoutRole`, the span/row → role
 * baseline), so the skin never re-derives prominence — it only chooses the
 * spatial footprint of a role it advertises.
 */

import type { Device, Role, SkinLayout } from '@obs/visu-contract';
import { layoutRole, byId as modelById, type RoomGroup, type LayoutEntry } from '../core/model';

/** A grid footprint: column span `c` and row span `r` (both ≥ 1). */
export interface GridSpan {
  readonly c: number;
  readonly r: number;
}

/** The deterministic floor footprint when role is not honoured / not mapped. */
const UNIT_SPAN: GridSpan = { c: 1, r: 1 };

/** Column profile derived from the manifest grid (with safe defaults). */
export interface ColumnProfile {
  readonly min: number;
  readonly max: number;
  readonly default: number;
  readonly configurable: boolean;
}

const DEFAULT_COLUMNS: ColumnProfile = { min: 1, max: 6, default: 3, configurable: false };

/**
 * One placed item the renderer loop consumes. Carries the core handles plus the
 * additive spatial hints. `group`/`firstInGroup` make grouping a render concern
 * the skin cannot lose; `span` is the (ignorable) role footprint.
 */
export interface PlacedItem {
  readonly id: string;
  readonly role: Role;
  /** Room/group label this item belongs to (grouping is part of the floor). */
  readonly group: string;
  /** True for the first item of a group — lets a skin draw the group boundary. */
  readonly firstInGroup: boolean;
  /** Grid footprint for this item. In `list` model (or unmapped) this is 1×1. */
  readonly span: GridSpan;
}

/** The resolved layout the host hands to the renderer loop. */
export interface ResolvedLayout {
  /** "grid" | "list" | … — taken verbatim from the manifest. */
  readonly model: string;
  /** Whether the resolved model honours role footprints (else everything is 1×1). */
  readonly honorsRole: boolean;
  /** The column profile (grid model only; sane defaults otherwise). */
  readonly columns: ColumnProfile;
  /** Items in strict source order, with grouping + (additive) span. */
  readonly items: readonly PlacedItem[];
}

/** Does the manifest's layout list `name` in its `honors`? */
function honors(layout: SkinLayout, name: string): boolean {
  return Array.isArray(layout.honors) && layout.honors.includes(name);
}

/** Read the column profile from the manifest grid block, falling back to defaults. */
function readColumns(layout: SkinLayout): ColumnProfile {
  const grid = (layout.grid ?? {}) as Record<string, unknown>;
  const c = grid.columns as Partial<ColumnProfile> | undefined;
  if (!c) return DEFAULT_COLUMNS;
  return {
    min: typeof c.min === 'number' ? c.min : DEFAULT_COLUMNS.min,
    max: typeof c.max === 'number' ? c.max : DEFAULT_COLUMNS.max,
    default: typeof c.default === 'number' ? c.default : DEFAULT_COLUMNS.default,
    configurable: c.configurable === true,
  };
}

/**
 * Translate a role to its grid footprint using the manifest's `roleMap`.
 * Total: a role missing from the map degrades to {@link UNIT_SPAN} (the floor),
 * so an unmapped role is never broken — just a plain ordered cell.
 */
function spanForRole(layout: SkinLayout, role: Role): GridSpan {
  const map = (layout.roleMap ?? {}) as Record<string, Partial<GridSpan>>;
  const m = map[role];
  if (!m || typeof m.c !== 'number' || typeof m.r !== 'number') return UNIT_SPAN;
  return { c: m.c, r: m.r };
}

/** Clamp a requested column count into the profile's [min, max] window. */
export function clampColumns(profile: ColumnProfile, requested: number): number {
  if (!Number.isFinite(requested)) return profile.default;
  return Math.min(profile.max, Math.max(profile.min, Math.round(requested)));
}

/** Resolve a layout entry's device — the host owns state, so this looks it up. */
export type DeviceResolver = (id: string) => Device | undefined;

/** Default resolver: the static core model (`model.byId`) — the seed source. */
const modelResolver: DeviceResolver = (id) => modelById[id];

/**
 * Resolve the room groups (order + grouping = the floor) against a skin's
 * `manifest.layout` into a flat, ordered list of placed items.
 *
 * Pure: reads only its inputs + the manifest, owns no state. The result is the
 * same item order regardless of model; only the `span` (role footprint) differs
 * between a role-honouring grid and a flat list. Role is derived from the device
 * via the core `layoutRole` baseline — the layout never invents prominence.
 *
 * @param layout    the skin's `manifest.layout` block
 * @param groups    the ordered room groups
 * @param resolve   device lookup (defaults to the static core model); the host
 *                  passes its live store lookup so the role footprint tracks the
 *                  live device type.
 */
export function resolveLayout(
  layout: SkinLayout,
  groups: readonly RoomGroup[],
  resolve: DeviceResolver = modelResolver,
): ResolvedLayout {
  // role footprints only apply to a grid model that advertises "role"; a list
  // model (or one that does not honour role) degrades every item to 1×1.
  const honorsRole = layout.model === 'grid' && honors(layout, 'role');
  const items: PlacedItem[] = [];

  for (const group of groups) {
    let first = true;
    for (const entry of group.entries) {
      const device = resolve(entry.id);
      // An entry that references no known device is an authoring gap, not a
      // silent skip — surface it (same discipline as the renderer dispatch).
      if (!device) {
        throw new Error(
          `skin-host/layout: layout entry "${entry.id}" references no device in the core model.`,
        );
      }
      const role = layoutRole(entry, device);
      items.push({
        id: entry.id,
        role,
        group: group.room,
        firstInGroup: first,
        span: honorsRole ? spanForRole(layout, role) : UNIT_SPAN,
      });
      first = false;
    }
  }

  return {
    model: layout.model,
    honorsRole,
    columns: readColumns(layout),
    items,
  };
}

export type { RoomGroup, LayoutEntry };
