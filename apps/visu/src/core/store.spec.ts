import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import type {
  Device,
  LightDevice,
  SwitchDevice,
  BlindDevice,
  JalousieDevice,
  SceneDevice,
  WidgetAction,
} from '@obs/visu-contract';

import { MockDataSource, type DataSource, type PatchListener, type DevicePatch } from './datasource';
import { useDeviceStore } from './store';

/**
 * core/store — the Pinia host store (CO3, Issue #93).
 *
 * CONTRACT-v1 §6: the host owns the device state. The store seeds itself from a
 * {@link DataSource}, subscribes to live feedback, and exposes the canonical
 * actions. Every action goes through `dataSource.dispatch` + an optimistic local
 * update; `subscribe` writes real Rückmeldungen back into the same state.
 *
 * Goldene Regeln honoured here:
 *  - State lives in core; the store is its single owner, mutated ONLY by actions.
 *  - No state mutation outside the actions.
 *  - Imports no skin/renderer — only the model/contract + the data source.
 */

/** A spy DataSource that records dispatches and lets the test push patches. */
class SpyDataSource implements DataSource {
  readonly dispatched: Array<{ id: string; action: WidgetAction; payload?: unknown }> = [];
  private listeners = new Set<PatchListener>();
  private inner: MockDataSource;

  constructor(seed?: readonly Device[]) {
    this.inner = seed ? new MockDataSource(seed) : new MockDataSource();
  }

  list(): Promise<Device[]> {
    return this.inner.list();
  }

  subscribe(cb: PatchListener): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  dispatch(id: string, action: WidgetAction, payload?: unknown): Promise<void> {
    this.dispatched.push({ id, action, payload });
    return Promise.resolve();
  }

  /** Simulate a backend Rückmeldung. */
  push(patch: DevicePatch): void {
    for (const cb of this.listeners) cb(patch);
  }
}

async function makeStore(ds: DataSource = new MockDataSource()) {
  const store = useDeviceStore();
  await store.init(ds);
  return store;
}

function firstId(store: ReturnType<typeof useDeviceStore>, type: Device['type']): string {
  const d = store.devices.find((x: Device) => x.type === type);
  if (!d?.id) throw new Error(`no device of type ${type}`);
  return d.id;
}

function dimmableId(store: ReturnType<typeof useDeviceStore>): string {
  const d = store.devices.find((x: Device) => x.type === 'light' && (x as LightDevice).dim !== null);
  if (!d?.id) throw new Error('no dimmable light');
  return d.id;
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe('core/store — init() + state ownership', () => {
  it('seeds devices from the data source', async () => {
    const store = await makeStore();
    expect(store.devices.length).toBeGreaterThan(0);
    expect(store.devices.every((d: Device) => typeof d.id === 'string')).toBe(true);
  });

  it('byId resolves a device by id', async () => {
    const store = await makeStore();
    const id = firstId(store, 'light');
    expect(store.byId(id)?.id).toBe(id);
    expect(store.byId('nope')).toBeUndefined();
  });
});

describe('core/store — subscribe() writes feedback into state', () => {
  it('merges a backend patch into the matching device', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = firstId(store, 'light');
    ds.push({ id, changes: { on: true, dim: 33 } });
    const d = store.byId(id) as LightDevice;
    expect(d.on).toBe(true);
    expect(d.dim).toBe(33);
  });

  it('ignores a patch for an unknown id', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const before = store.devices.length;
    ds.push({ id: 'ghost', changes: { on: true } });
    expect(store.devices.length).toBe(before);
  });
});

describe('core/store — toggle()', () => {
  it('flips a switch on-state optimistically and dispatches toggle', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = firstId(store, 'switch');
    const before = (store.byId(id) as SwitchDevice).on;
    await store.toggle(id);
    expect((store.byId(id) as SwitchDevice).on).toBe(!before);
    expect(ds.dispatched).toContainEqual({ id, action: 'toggle', payload: undefined });
  });

  it('turning a light on while dim===0 sets dim to 60 (widgets.js → tap)', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    // find an off light with dim === 0
    const target = store.devices.find(
      (d: Device) => d.type === 'light' && !(d as LightDevice).on && (d as LightDevice).dim === 0,
    ) as LightDevice;
    expect(target?.id).toBeTruthy();
    await store.toggle(target.id!);
    const after = store.byId(target.id!) as LightDevice;
    expect(after.on).toBe(true);
    expect(after.dim).toBe(60);
    // the canonical action wired is setDim(60), which sets dim + on
    expect(ds.dispatched).toContainEqual({
      id: target.id,
      action: 'setDim',
      payload: { value: 60 },
    });
  });

  it('turning a dimmed light off via toggle leaves dim untouched and dispatches toggle', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = dimmableId(store);
    // bring it on at a non-zero dim first
    await store.setDim(id, 45);
    ds.dispatched.length = 0;
    await store.toggle(id);
    const after = store.byId(id) as LightDevice;
    expect(after.on).toBe(false);
    expect(after.dim).toBe(45);
    expect(ds.dispatched).toContainEqual({ id, action: 'toggle', payload: undefined });
  });

  it('a non-dimmable light (dim===null) toggles plainly', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const target = store.devices.find(
      (d: Device) => d.type === 'light' && (d as LightDevice).dim === null,
    ) as LightDevice;
    expect(target?.id).toBeTruthy();
    const before = target.on;
    await store.toggle(target.id!);
    expect((store.byId(target.id!) as LightDevice).on).toBe(!before);
    expect(ds.dispatched).toContainEqual({ id: target.id, action: 'toggle', payload: undefined });
  });
});

describe('core/store — setDim()', () => {
  it('clamps to 0..100 and turns the light on when > 0', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = dimmableId(store);
    await store.setDim(id, 150);
    let d = store.byId(id) as LightDevice;
    expect(d.dim).toBe(100);
    expect(d.on).toBe(true);
    await store.setDim(id, -10);
    d = store.byId(id) as LightDevice;
    expect(d.dim).toBe(0);
    expect(d.on).toBe(false);
    expect(ds.dispatched).toContainEqual({ id, action: 'setDim', payload: { value: 100 } });
    expect(ds.dispatched).toContainEqual({ id, action: 'setDim', payload: { value: 0 } });
  });
});

describe('core/store — setPosition()', () => {
  it('clamps a blind position 0..100 and dispatches setPosition', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = firstId(store, 'blind');
    await store.setPosition(id, 200);
    expect((store.byId(id) as BlindDevice).position).toBe(100);
    await store.setPosition(id, -50);
    expect((store.byId(id) as BlindDevice).position).toBe(0);
    expect(ds.dispatched).toContainEqual({ id, action: 'setPosition', payload: { value: 100 } });
    expect(ds.dispatched).toContainEqual({ id, action: 'setPosition', payload: { value: 0 } });
  });

  it('a locked blind ignores tile operation (locked blockiert die Kachel)', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const lockedBlind = store.devices.find(
      (d: Device) => d.type === 'blind' && (d as BlindDevice).locked,
    ) as BlindDevice;
    expect(lockedBlind?.id).toBeTruthy();
    const before = lockedBlind.position;
    await store.setPosition(lockedBlind.id!, 80);
    expect((store.byId(lockedBlind.id!) as BlindDevice).position).toBe(before);
    expect(ds.dispatched).toHaveLength(0);
  });
});

describe('core/store — setSlat()', () => {
  it('clamps a jalousie slat 0..100 and dispatches setSlat', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = firstId(store, 'jalousie');
    await store.setSlat(id, 130);
    expect((store.byId(id) as JalousieDevice).slat).toBe(100);
    expect(ds.dispatched).toContainEqual({ id, action: 'setSlat', payload: { value: 100 } });
  });

  it('a locked jalousie ignores tile slat operation', async () => {
    const seed: JalousieDevice[] = [
      {
        id: 'jal-locked',
        type: 'jalousie',
        mode: 'jalousie',
        room: 'R',
        label: 'L',
        accent: 'green',
        position: 50,
        slat: 20,
        locked: true,
        statuses: [],
      },
    ];
    const dsl = new SpyDataSource(seed);
    const store = await makeStore(dsl);
    await store.setSlat('jal-locked', 90);
    expect((store.byId('jal-locked') as JalousieDevice).slat).toBe(20);
    expect(dsl.dispatched).toHaveLength(0);
  });
});

describe('core/store — lock() / unlock()', () => {
  it('lock sets locked; unlock clears it', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = firstId(store, 'blind');
    await store.lock(id);
    expect((store.byId(id) as BlindDevice).locked).toBe(true);
    await store.unlock(id);
    expect((store.byId(id) as BlindDevice).locked).toBe(false);
    expect(ds.dispatched).toContainEqual({ id, action: 'lock', payload: undefined });
    expect(ds.dispatched).toContainEqual({ id, action: 'unlock', payload: undefined });
  });

  it('unlock works on a locked device even though the tile is otherwise blocked', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const lockedBlind = store.devices.find(
      (d: Device) => d.type === 'blind' && (d as BlindDevice).locked,
    ) as BlindDevice;
    expect(lockedBlind?.id).toBeTruthy();
    await store.unlock(lockedBlind.id!);
    expect((store.byId(lockedBlind.id!) as BlindDevice).locked).toBe(false);
  });
});

describe('core/store — activateScene()', () => {
  it('dispatches activateScene for a scene device', async () => {
    const seed: SceneDevice[] = [
      { id: 'scene-1', type: 'scene', room: 'Szenen', label: 'Film', accent: 'violet', icon: 'sparkle' },
    ];
    const ds = new SpyDataSource(seed);
    const store = await makeStore(ds);
    await store.activateScene('scene-1');
    expect(ds.dispatched).toContainEqual({ id: 'scene-1', action: 'activateScene', payload: undefined });
  });
});

describe('core/store — alarm arm/disarm (v1.1 stub)', () => {
  it('arm / disarm dispatch the canonical action without throwing', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    // v1.1 stub: actions exist and dispatch; no core alarm device in v1 model.
    await store.arm('alarm-x');
    await store.disarm('alarm-x');
    expect(ds.dispatched).toContainEqual({ id: 'alarm-x', action: 'arm', payload: undefined });
    expect(ds.dispatched).toContainEqual({ id: 'alarm-x', action: 'disarm', payload: undefined });
  });
});

describe('core/store — optimistic update + backend correction', () => {
  it('applies optimistically, then a subscribe patch corrects the state', async () => {
    const ds = new SpyDataSource();
    const store = await makeStore(ds);
    const id = firstId(store, 'light');
    await store.toggle(id);
    const optimistic = (store.byId(id) as LightDevice).on;
    // backend corrects to the opposite of the optimistic value
    ds.push({ id, changes: { on: !optimistic } });
    expect((store.byId(id) as LightDevice).on).toBe(!optimistic);
  });
});
