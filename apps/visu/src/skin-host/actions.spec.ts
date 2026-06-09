import { describe, it, expect, vi } from 'vitest';
import type { Device, JalousieDevice } from '@obs/visu-contract';

import {
  parseIntent,
  dispatchIntent,
  type ActionStore,
  type HostAction,
} from './actions';

/**
 * skin-host/actions — gestures → canonical core-store actions (A2, Issue #98).
 *
 * Golden rule 4: the SKIN owns no state. A skin renderer only marks an intent on
 * a DOM node via `data-action` (+ `data-arg`/`data-value`/`data-relative`); this
 * module is the single host seam that turns such a marker — plus a device id and
 * (for sliders) an event value — into a canonical core-store action call.
 *
 * The store is addressed structurally ({@link ActionStore}) so the mapping is
 * unit-testable without Pinia/Vue.
 */

/** A spy store implementing exactly the canonical action surface the host calls. */
function makeStore(devices: readonly Device[] = []) {
  const byId = new Map(devices.map((d) => [d.id as string, d]));
  return {
    toggle: vi.fn(),
    setDim: vi.fn(),
    setPosition: vi.fn(),
    setSlat: vi.fn(),
    lock: vi.fn(),
    unlock: vi.fn(),
    activateScene: vi.fn(),
    arm: vi.fn(),
    disarm: vi.fn(),
    byId: vi.fn((id: string) => byId.get(id)),
  } satisfies ActionStore & { byId: (id: string) => Device | undefined };
}

/** Build a DOM element carrying a skin's data-action markers. */
function el(attrs: Record<string, string>): HTMLElement {
  const node = document.createElement('button');
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

describe('parseIntent — reads the skin marker off a DOM element', () => {
  it('returns null for an element without data-action (a non-interactive node)', () => {
    expect(parseIntent(el({}))).toBeNull();
  });

  it('reads a bare canonical action (toggle) with no argument', () => {
    expect(parseIntent(el({ 'data-action': 'toggle' }))).toEqual({
      action: 'toggle',
      arg: undefined,
      relative: false,
    });
  });

  it('reads data-value as the numeric argument (LightDetail convention)', () => {
    expect(parseIntent(el({ 'data-action': 'setDim', 'data-value': '60' }))).toEqual({
      action: 'setDim',
      arg: 60,
      relative: false,
    });
  });

  it('reads data-arg as the numeric argument (Blind/Jalousie convention)', () => {
    expect(parseIntent(el({ 'data-action': 'setPosition', 'data-arg': '100' }))).toEqual({
      action: 'setPosition',
      arg: 100,
      relative: false,
    });
  });

  it('flags data-relative for delta steps (stepOpen/stepClose)', () => {
    expect(
      parseIntent(el({ 'data-action': 'setPosition', 'data-arg': '10', 'data-relative': '1' })),
    ).toEqual({ action: 'setPosition', arg: 10, relative: true });
  });

  it('finds the nearest [data-action] ancestor when the click lands on a child', () => {
    const wrap = el({ 'data-action': 'toggle' });
    const child = document.createElement('span');
    wrap.appendChild(child);
    expect(parseIntent(child)).toEqual({ action: 'toggle', arg: undefined, relative: false });
  });
});

describe('dispatchIntent — maps a canonical action to the right store call', () => {
  it('toggle → store.toggle(id)', () => {
    const store = makeStore();
    dispatchIntent(store, 'light-1', { action: 'toggle', relative: false });
    expect(store.toggle).toHaveBeenCalledWith('light-1');
  });

  it('setDim with absolute arg → store.setDim(id, value)', () => {
    const store = makeStore();
    dispatchIntent(store, 'l', { action: 'setDim', arg: 60, relative: false });
    expect(store.setDim).toHaveBeenCalledWith('l', 60);
  });

  it('setDim from a slider event value (no data-arg) uses the event value', () => {
    const store = makeStore();
    dispatchIntent(store, 'l', { action: 'setDim', relative: false }, 42);
    expect(store.setDim).toHaveBeenCalledWith('l', 42);
  });

  it('setPosition absolute → store.setPosition(id, value)', () => {
    const store = makeStore();
    dispatchIntent(store, 'b', { action: 'setPosition', arg: 100, relative: false });
    expect(store.setPosition).toHaveBeenCalledWith('b', 100);
  });

  it('setPosition relative resolves the delta against the live device position', () => {
    const dev = { type: 'blind', id: 'b', room: 'r', label: 'L', accent: 'orange', position: 30, locked: false } as Device;
    const store = makeStore([dev]);
    dispatchIntent(store, 'b', { action: 'setPosition', arg: 10, relative: true });
    expect(store.setPosition).toHaveBeenCalledWith('b', 40);
  });

  it('setSlat relative resolves the delta against the live slat angle', () => {
    const dev = {
      type: 'jalousie', mode: 'jalousie', id: 'j', room: 'r', label: 'L', accent: 'green',
      position: 0, slat: 50, locked: false, statuses: [],
    } as JalousieDevice;
    const store = makeStore([dev]);
    dispatchIntent(store, 'j', { action: 'setSlat', arg: -15, relative: true });
    expect(store.setSlat).toHaveBeenCalledWith('j', 35);
  });

  it('lock / unlock map to the lock actions', () => {
    const store = makeStore();
    dispatchIntent(store, 'b', { action: 'lock', relative: false });
    dispatchIntent(store, 'b', { action: 'unlock', relative: false });
    expect(store.lock).toHaveBeenCalledWith('b');
    expect(store.unlock).toHaveBeenCalledWith('b');
  });

  it('activateScene → store.activateScene(id)', () => {
    const store = makeStore();
    dispatchIntent(store, 's', { action: 'activateScene', relative: false });
    expect(store.activateScene).toHaveBeenCalledWith('s');
  });

  it('the UI-only "stop" action is a no-op on the store (no canonical write)', () => {
    const store = makeStore();
    dispatchIntent(store, 'b', { action: 'stop', relative: false });
    for (const fn of [store.toggle, store.setDim, store.setPosition, store.setSlat, store.lock, store.unlock])
      expect(fn).not.toHaveBeenCalled();
  });

  it('the host-only "close"/"openDetail" actions never touch the store', () => {
    const store = makeStore();
    dispatchIntent(store, 'b', { action: 'close', relative: false });
    dispatchIntent(store, 'b', { action: 'openDetail', relative: false });
    for (const fn of [store.toggle, store.setPosition, store.setDim])
      expect(fn).not.toHaveBeenCalled();
  });

  it('a relative step with no resolvable device falls back to the bare arg', () => {
    const store = makeStore();
    dispatchIntent(store, 'missing', { action: 'setPosition', arg: 10, relative: true });
    expect(store.setPosition).toHaveBeenCalledWith('missing', 10);
  });

  it('setDim with neither arg nor event value is ignored (nothing to set)', () => {
    const store = makeStore();
    dispatchIntent(store, 'l', { action: 'setDim', relative: false });
    expect(store.setDim).not.toHaveBeenCalled();
  });
});

describe('isUiOnly / isDetailTrigger — host classifies the gesture', () => {
  it('marks stop/close as UI-only (no canonical store action)', async () => {
    const { isUiOnly } = await import('./actions');
    expect(isUiOnly('stop')).toBe(true);
    expect(isUiOnly('close')).toBe(true);
    expect(isUiOnly('toggle')).toBe(false);
  });

  it('marks openDetail as the detail trigger', async () => {
    const { isDetailTrigger } = await import('./actions');
    expect(isDetailTrigger('openDetail')).toBe(true);
    expect(isDetailTrigger('toggle')).toBe(false);
  });
});

describe('type surface', () => {
  it('HostAction includes the canonical + ui-only + host actions', () => {
    const a: HostAction[] = [
      'toggle', 'setDim', 'setPosition', 'setSlat', 'lock', 'unlock',
      'activateScene', 'arm', 'disarm', 'stop', 'close', 'openDetail',
    ];
    expect(a).toHaveLength(12);
  });
});
