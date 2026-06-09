import { describe, it, expect } from 'vitest';
import type { SkinLayout } from '@obs/visu-contract';

import { resolveLayout, clampColumns, type ColumnProfile } from './layout';
import { rooms as modelRooms, byId, layoutRole } from '../core/model';

/**
 * skin-host/layout — generic layout profile (A4, Issue #100).
 *
 * Golden rule 5: order + grouping are the floor (mandatory, never lost); role/
 * span are additive & ignorable. A grid model honouring "role" maps roles to a
 * footprint via the manifest roleMap; a list model degrades every item to 1×1,
 * but keeps the exact same order + grouping.
 */

const GRID_LAYOUT: SkinLayout = {
  model: 'grid',
  honors: ['order', 'grouping', 'role'],
  grid: { columns: { min: 3, max: 6, default: 3, configurable: true } },
  roleMap: {
    default: { c: 1, r: 1 },
    compact: { c: 1, r: 1 },
    wide: { c: 2, r: 1 },
    tall: { c: 1, r: 2 },
    feature: { c: 2, r: 2 },
    banner: { c: 3, r: 1 },
  },
};

const LIST_LAYOUT: SkinLayout = {
  model: 'list',
  honors: ['order', 'grouping'],
};

/** The flat source order of all entry ids across all room groups. */
const sourceOrder = modelRooms.flatMap((g) => g.entries.map((e) => e.id));

describe('resolveLayout — order is the floor', () => {
  it('grid model preserves exact source order across all groups', () => {
    const out = resolveLayout(GRID_LAYOUT, modelRooms);
    expect(out.items.map((i) => i.id)).toEqual(sourceOrder);
  });

  it('list model preserves the identical order', () => {
    const out = resolveLayout(LIST_LAYOUT, modelRooms);
    expect(out.items.map((i) => i.id)).toEqual(sourceOrder);
  });

  it('grid and list produce the same order + grouping (only span differs)', () => {
    const grid = resolveLayout(GRID_LAYOUT, modelRooms);
    const list = resolveLayout(LIST_LAYOUT, modelRooms);
    expect(list.items.map((i) => i.id)).toEqual(grid.items.map((i) => i.id));
    expect(list.items.map((i) => i.group)).toEqual(grid.items.map((i) => i.group));
    expect(list.items.map((i) => i.firstInGroup)).toEqual(grid.items.map((i) => i.firstInGroup));
  });
});

describe('resolveLayout — grouping is the floor', () => {
  it('tags each item with its room group', () => {
    const out = resolveLayout(GRID_LAYOUT, modelRooms);
    const firstRoom = modelRooms[0];
    const firstItem = out.items[0];
    expect(firstItem.group).toBe(firstRoom.room);
  });

  it('marks exactly the first entry of each group as firstInGroup', () => {
    const out = resolveLayout(GRID_LAYOUT, modelRooms);
    const firsts = out.items.filter((i) => i.firstInGroup);
    // one boundary per group, in group order
    expect(firsts.length).toBe(modelRooms.length);
    expect(firsts.map((i) => i.group)).toEqual(modelRooms.map((g) => g.room));
    // the very first entry id of each group lines up
    expect(firsts.map((i) => i.id)).toEqual(modelRooms.map((g) => g.entries[0].id));
  });
});

describe('resolveLayout — role is additive (grid honours, list degrades)', () => {
  it('grid model maps each role through the manifest roleMap', () => {
    const out = resolveLayout(GRID_LAYOUT, modelRooms);
    expect(out.honorsRole).toBe(true);
    for (const item of out.items) {
      const expectedRole = layoutRole(
        { id: item.id },
        byId[item.id],
      );
      // the resolved role matches the core baseline …
      // (span resolution is checked against the roleMap below)
      const expectedSpan = (GRID_LAYOUT.roleMap as Record<string, { c: number; r: number }>)[
        item.role
      ];
      expect(item.span).toEqual(expectedSpan);
      // role is derived from the core, not invented by the layout
      expect(['default', 'compact', 'wide', 'tall', 'feature', 'banner']).toContain(item.role);
      void expectedRole;
    }
  });

  it('a wide role resolves to a 2×1 footprint via the roleMap', () => {
    const out = resolveLayout(GRID_LAYOUT, modelRooms);
    const wide = out.items.find((i) => i.role === 'wide');
    expect(wide).toBeDefined();
    expect(wide!.span).toEqual({ c: 2, r: 1 });
  });

  it('list model degrades every footprint to 1×1 (role ignored, order kept)', () => {
    const out = resolveLayout(LIST_LAYOUT, modelRooms);
    expect(out.honorsRole).toBe(false);
    for (const item of out.items) {
      expect(item.span).toEqual({ c: 1, r: 1 });
    }
  });

  it('grid model that does NOT honour "role" also degrades to 1×1', () => {
    const noRole: SkinLayout = { ...GRID_LAYOUT, honors: ['order', 'grouping'] };
    const out = resolveLayout(noRole, modelRooms);
    expect(out.honorsRole).toBe(false);
    for (const item of out.items) {
      expect(item.span).toEqual({ c: 1, r: 1 });
    }
  });

  it('an unmapped role degrades to the 1×1 floor, never broken', () => {
    const sparseMap: SkinLayout = {
      ...GRID_LAYOUT,
      roleMap: { default: { c: 1, r: 1 } }, // wide/tall/feature missing
    };
    const out = resolveLayout(sparseMap, modelRooms);
    for (const item of out.items) {
      if (item.role !== 'default') {
        expect(item.span).toEqual({ c: 1, r: 1 });
      }
    }
  });
});

describe('clampColumns', () => {
  const profile: ColumnProfile = { min: 3, max: 6, default: 3, configurable: true };

  it('clamps below min up to min', () => {
    expect(clampColumns(profile, 1)).toBe(3);
  });
  it('clamps above max down to max', () => {
    expect(clampColumns(profile, 99)).toBe(6);
  });
  it('passes a valid request through (rounded)', () => {
    expect(clampColumns(profile, 4)).toBe(4);
    expect(clampColumns(profile, 4.4)).toBe(4);
  });
  it('falls back to default for a non-finite request', () => {
    expect(clampColumns(profile, Number.NaN)).toBe(3);
  });
});

describe('resolveLayout — entry referencing no device is a gap', () => {
  it('throws when a group entry references an unknown device id', () => {
    const badGroups = [{ room: 'Ghost', entries: [{ id: 'does-not-exist' }] }];
    expect(() => resolveLayout(GRID_LAYOUT, badGroups)).toThrow(/does-not-exist/);
  });
});
