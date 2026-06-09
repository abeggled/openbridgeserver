import { describe, it, expect } from 'vitest';
import { fixtures } from '@obs/visu-contract';
import type { Device } from '@obs/visu-contract';

import {
  devices,
  byId,
  rooms,
  layoutRole,
  type RoomGroup,
  type LayoutEntry,
} from './model';

/**
 * core/model — device/room model (CO1, Issue #91).
 *
 * Ported from reference/vue-ionic/store.js (device dataset + mobileGroups).
 * Data shapes follow CONTRACT-v1 §3; the model is read-only to the outside
 * and imports no skins (Goldene Regeln 1 + "Renderer rein").
 */

describe('core/model — loads', () => {
  it('exposes a non-empty device list (ported from store.js)', () => {
    expect(Array.isArray(devices)).toBe(true);
    expect(devices.length).toBeGreaterThan(0);
  });

  it('only carries stable core v1 widget types (no reserved/tablet-only types)', () => {
    const coreTypes = new Set(['light', 'switch', 'blind', 'jalousie', 'sensor', 'scene']);
    for (const d of devices) {
      expect(coreTypes.has(d.type)).toBe(true);
    }
  });

  it('every device declares the contract base fields (id/room/label/accent)', () => {
    for (const d of devices) {
      expect(typeof d.id).toBe('string');
      expect(d.id).not.toBe('');
      expect(typeof d.room).toBe('string');
      expect(typeof d.label).toBe('string');
      expect(typeof d.accent).toBe('string');
    }
  });
});

describe('core/model — byId', () => {
  it('indexes every device by its id', () => {
    for (const d of devices) {
      expect(byId[d.id!]).toBe(d);
    }
    expect(Object.keys(byId).length).toBe(devices.length);
  });

  it('resolves a known id to the matching device', () => {
    const light = byId['kueche-wand'];
    expect(light).toBeDefined();
    expect(light.type).toBe('light');
    expect(light.room).toBe('EG Küche');
  });

  it('has no duplicate ids', () => {
    const ids = devices.map((d) => d.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('core/model — room grouping', () => {
  it('groups devices into ordered room blocks (mobileGroups)', () => {
    expect(Array.isArray(rooms)).toBe(true);
    expect(rooms.length).toBeGreaterThan(0);
    const names = rooms.map((g) => g.room);
    expect(names).toEqual([
      'Küche',
      'WC & Bad',
      'Wintergarten',
      'Schlafzimmer',
      'Wohnzimmer',
      'Gäste & Treppe',
    ]);
  });

  it('every grouped entry references a real device by id', () => {
    for (const group of rooms) {
      for (const entry of group.entries) {
        expect(byId[entry.id]).toBeDefined();
      }
    }
  });

  it('preserves the store.js order within a room (grouping + order as the floor)', () => {
    const kueche = rooms.find((g) => g.room === 'Küche') as RoomGroup;
    expect(kueche.entries.map((e) => e.id)).toEqual([
      'kueche-wand',
      'kueche-pendel',
      'kueche-arbeit',
      'kueche-roll',
    ]);
  });

  it('covers exactly the devices reachable from the room groups', () => {
    const grouped = new Set(rooms.flatMap((g) => g.entries.map((e) => e.id)));
    expect(grouped.size).toBe(devices.length);
    for (const d of devices) {
      expect(grouped.has(d.id!)).toBe(true);
    }
  });
});

describe('core/model — span/row → role', () => {
  it('maps a plain entry to the device type default role', () => {
    const plain: LayoutEntry = { id: 'kueche-wand' };
    expect(layoutRole(plain, byId[plain.id])).toBe('default');
  });

  it('maps a wide span hint to the "wide" role', () => {
    const wide: LayoutEntry = { id: 'wiga-pendel', span: 2 };
    expect(layoutRole(wide, byId[wide.id])).toBe('wide');
  });

  it('keeps the jalousie default role even with span/row hints', () => {
    const jal: LayoutEntry = { id: 'wiga-jalousie', span: 2, row: 3 };
    expect(layoutRole(jal, byId[jal.id])).toBe('wide');
  });
});

describe('core/model — shapes match the contract fixtures', () => {
  it('a light device matches the light fixture field set', () => {
    const fixtureLight = (fixtures as unknown as Record<string, Record<string, Device>>).light
      .dimmed;
    const modelLight = byId['kueche-pendel'];
    expect(modelLight.type).toBe('light');
    // Same data fields as the contract fixture (plus the runtime id/type).
    for (const key of Object.keys(fixtureLight)) {
      expect(modelLight).toHaveProperty(key);
    }
  });

  it('a jalousie device matches the jalousie fixture field set', () => {
    const fixtureJal = (fixtures as unknown as Record<string, Record<string, Device>>).jalousie
      .tilted;
    const modelJal = byId['wiga-jalousie'];
    expect(modelJal.type).toBe('jalousie');
    for (const key of Object.keys(fixtureJal)) {
      expect(modelJal).toHaveProperty(key);
    }
  });
});

describe('core/model — read-only to the outside (Goldene Regel 1)', () => {
  it('freezes the device list and byId index', () => {
    expect(Object.isFrozen(devices)).toBe(true);
    expect(Object.isFrozen(byId)).toBe(true);
    expect(Object.isFrozen(rooms)).toBe(true);
  });
});
