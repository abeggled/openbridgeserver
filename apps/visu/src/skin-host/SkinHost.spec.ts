import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { mount } from '@vue/test-utils';
import type { Device } from '@obs/visu-contract';

import SkinHost from './SkinHost';
import { useDeviceStore } from '../core/store';
import { MockDataSource } from '../core/datasource';
import type { RoomGroup } from '../core/model';

/**
 * skin-host/SkinHost — the host render component (A1 + A4, Issues #97 + #100).
 *
 * Ties the skin registry + layout profile + type dispatch together: live device
 * state from the store, AA tokens, type-addressed renderers, order + grouping as
 * the floor. A page that asks for a type the skin neither renders nor declared
 * unsupported throws a visible gap (golden rules 2/3).
 */

async function seedStore(devices?: readonly Device[]): Promise<void> {
  const store = useDeviceStore();
  await store.init(devices ? new MockDataSource(devices) : new MockDataSource());
}

describe('SkinHost — present types render (ionic, real model)', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('renders one cell per layout item, in source order, grouped', async () => {
    await seedStore();
    // a small ordered, grouped page using real model device ids
    const groups: RoomGroup[] = [
      { room: 'Küche', entries: [{ id: 'kueche-wand' }, { id: 'kueche-roll' }] },
      { room: 'WC', entries: [{ id: 'wc-luefter' }] },
    ];

    const wrapper = mount(SkinHost, { props: { skin: 'ionic', groups, theme: 'light' } });

    const cells = wrapper.findAll('.skin-host-cell');
    expect(cells.length).toBe(3);

    // order preserved
    // grouping carried as data-group; first item of each group flagged
    const groupStarts = wrapper.findAll('.skin-host-group-start');
    expect(groupStarts.length).toBe(2);
    expect(cells[0].attributes('data-group')).toBe('Küche');
    expect(cells[2].attributes('data-group')).toBe('WC');
  });

  it('exposes the clamped column count as a CSS variable on the grid root', async () => {
    await seedStore();
    const groups: RoomGroup[] = [{ room: 'Küche', entries: [{ id: 'kueche-wand' }] }];
    // request 99 columns — must clamp into the ionic window (max 6)
    const wrapper = mount(SkinHost, { props: { skin: 'ionic', groups, columns: 99 } });
    const root = wrapper.find('.skin-host');
    expect(root.attributes('style')).toContain('--skin-host-columns: 6');
  });
});

describe('SkinHost — gap throws on render', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('throws a visible gap when an item type is neither rendered nor unsupported', async () => {
    // seed a device of a type the ionic skin neither ships nor declares unsupported.
    // ionic.unsupported = ["camera","media"]; "alarm" is reserved but NOT declared,
    // so a page asking for it is a gap the host must surface.
    const alarm = {
      type: 'alarm',
      id: 'ghost-alarm',
      room: 'Ghost',
      label: 'Alarm',
      accent: 'orange',
    } as unknown as Device;
    await seedStore([alarm]);

    const groups: RoomGroup[] = [{ room: 'Ghost', entries: [{ id: 'ghost-alarm' }] }];

    expect(() => mount(SkinHost, { props: { skin: 'ionic', groups } })).toThrow(/gap/i);
  });
});
