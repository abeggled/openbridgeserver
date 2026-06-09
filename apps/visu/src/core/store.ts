/**
 * core/store — the Pinia host store for the obs Visu mobile app (CO3, Issue #93).
 *
 * CONTRACT-v1 §6: **the host owns the device state.** This store is that single
 * owner. It seeds itself from a {@link DataSource}, subscribes to live feedback,
 * and exposes the canonical actions (toggle · setDim · setPosition · setSlat ·
 * lock · unlock · activateScene; alarm arm/disarm as a v1.1 stub).
 *
 * Each action follows the same seam (MIGRATION §4): it sends the canonical
 * intent through `dataSource.dispatch` **and** applies an optimistic local
 * update; the `subscribe` stream then writes the real backend Rückmeldungen
 * back into the same state. Today the MockDataSource resolves instantly and the
 * optimistic value is the truth; later a real transport confirms or corrects it.
 *
 * Goldene Regeln honoured here:
 *  - **State lives in core.** The reactive `byId` map is the single source of
 *    truth for live device state; renderers/skins never touch it directly.
 *  - **No mutation outside the actions.** Every write to the map happens inside
 *    a store action (or the subscribe handler the store installs); callers send
 *    intents, they never mutate a `Device`.
 *  - **Renderer rein:** this module imports no skin/renderer — only the model,
 *    the data/type contract, and the data source.
 *
 * Data and behaviour are kept apart: the seed data comes from the data source
 * (ultimately `model.ts`); the only code here is the action layer + the gesture
 * semantics the tiles need (`widgets.js → tap`/`stepBlind` lift the "dim===0 ⇒
 * 60 on enable" and the "locked blockiert die Kachel" rules here, not in skins).
 */

import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { Device, LightDevice, WidgetAction } from '@obs/visu-contract';
import type { DataSource, DevicePatch } from './datasource';
import { MockDataSource } from './datasource';

/** Brightness a light jumps to when switched on from a dimmed-to-zero state. */
const DEFAULT_ON_DIM = 60;

const clamp = (n: number, lo = 0, hi = 100): number => Math.max(lo, Math.min(hi, n));

/** Narrowing helpers — read-only, no mutation. */
function isLockable(d: Device | undefined): d is Device & { locked: boolean } {
  return !!d && (d.type === 'blind' || d.type === 'jalousie');
}

/**
 * The host store. Keyed by device id; `devices` is the source-order list, `byId`
 * the lookup screens use. All writes go through the actions below.
 */
export const useDeviceStore = defineStore('devices', () => {
  /** Live state, keyed by id (state lives in core). */
  const state = ref(new Map<string, Device>());
  /** Active data source + its unsubscribe handle. */
  let source: DataSource = new MockDataSource();
  let unsubscribe: (() => void) | null = null;

  /** Devices in source order (read-only view). */
  const devices = ref<Device[]>([]);

  function syncList(): void {
    devices.value = [...state.value.values()];
  }

  /** Apply a field patch to a device in the map. The ONLY place state is merged. */
  function merge(id: string, changes: Partial<Device>): void {
    const current = state.value.get(id);
    if (!current) return;
    const next = { ...current, ...changes } as Device;
    state.value.set(id, next);
    syncList();
  }

  /** Resolve a device by id (read-only). */
  function byId(id: string): Device | undefined {
    return state.value.get(id);
  }

  /**
   * Seed from a data source and subscribe to its live feedback. Idempotent:
   * a second call swaps the source and re-subscribes.
   */
  async function init(ds: DataSource = new MockDataSource()): Promise<void> {
    if (unsubscribe) unsubscribe();
    source = ds;
    const seed = await source.list();
    const map = new Map<string, Device>();
    for (const d of seed) {
      if (d.id) map.set(d.id, d);
    }
    state.value = map;
    syncList();
    // subscribe trägt echte Rückmeldungen ein (CONTRACT-v1 §6 / MIGRATION §4).
    unsubscribe = source.subscribe((patch: DevicePatch) => {
      merge(patch.id, patch.changes as Partial<Device>);
    });
  }

  /**
   * Send a canonical action to the source and optimistically apply the changes
   * locally. The single dispatch seam shared by every action below.
   */
  async function dispatch(
    id: string,
    action: WidgetAction,
    optimistic: Partial<Device>,
    payload?: unknown,
  ): Promise<void> {
    // Optimistic update first (the tile reacts immediately) …
    if (state.value.has(id)) merge(id, optimistic);
    // … then forward the intent; subscribe() will confirm/correct.
    await source.dispatch(id, action, payload);
  }

  /* ----------------------------------------------------- canonical actions */

  /**
   * Tap: switch a light/switch on/off. For a light that is off with `dim===0`,
   * switching on jumps to {@link DEFAULT_ON_DIM} via the canonical `setDim`
   * (widgets.js → tap); otherwise a plain `toggle`.
   */
  async function toggle(id: string): Promise<void> {
    const d = byId(id);
    if (!d || (d.type !== 'light' && d.type !== 'switch')) return;
    if (d.type === 'light' && !d.on && (d as LightDevice).dim === 0) {
      await setDim(id, DEFAULT_ON_DIM);
      return;
    }
    await dispatch(id, 'toggle', { on: !d.on } as Partial<Device>);
  }

  /** Set a light's brightness (0..100); on when > 0, off at 0. */
  async function setDim(id: string, pct: number): Promise<void> {
    const d = byId(id);
    if (!d || d.type !== 'light') return;
    const dim = clamp(pct);
    await dispatch(id, 'setDim', { dim, on: dim > 0 } as Partial<Device>, { value: dim });
  }

  /** Move a blind/jalousie to an absolute position (0=auf,100=zu). Locked tiles ignore it. */
  async function setPosition(id: string, pct: number): Promise<void> {
    const d = byId(id);
    if (!d || (d.type !== 'blind' && d.type !== 'jalousie')) return;
    if (isLockable(d) && d.locked) return; // locked blockiert die Kachel
    const position = clamp(pct);
    await dispatch(id, 'setPosition', { position } as Partial<Device>, { value: position });
  }

  /** Set a jalousie slat angle (0..100 ⇒ 0–90°). Locked tiles ignore it. */
  async function setSlat(id: string, pct: number): Promise<void> {
    const d = byId(id);
    if (!d || d.type !== 'jalousie') return;
    if (d.locked) return; // locked blockiert die Kachel
    const slat = clamp(pct);
    await dispatch(id, 'setSlat', { slat } as Partial<Device>, { value: slat });
  }

  /** Lock a blind/jalousie (blocks tile operation; unlock only in the detail). */
  async function lock(id: string): Promise<void> {
    const d = byId(id);
    if (!isLockable(d)) return;
    await dispatch(id, 'lock', { locked: true } as Partial<Device>);
  }

  /** Unlock a blind/jalousie. Allowed even though the locked tile is otherwise blocked. */
  async function unlock(id: string): Promise<void> {
    const d = byId(id);
    if (!isLockable(d)) return;
    await dispatch(id, 'unlock', { locked: false } as Partial<Device>);
  }

  /** Activate a scene (stateless intent — no local field changes). */
  async function activateScene(id: string): Promise<void> {
    const d = byId(id);
    if (!d || d.type !== 'scene') return;
    await dispatch(id, 'activateScene', {} as Partial<Device>);
  }

  /* --------------------------------------------- alarm arm/disarm (v1.1 stub) */
  // CONTRACT-v1 §6 reserves `alarm` for v1.1. No alarm device exists in the v1
  // core model, so these forward the canonical intent to the source without a
  // local optimistic field — a deliberate seam for when the type stabilises.

  async function arm(id: string): Promise<void> {
    await source.dispatch(id, 'arm');
  }

  async function disarm(id: string): Promise<void> {
    await source.dispatch(id, 'disarm');
  }

  return {
    devices,
    byId,
    init,
    toggle,
    setDim,
    setPosition,
    setSlat,
    lock,
    unlock,
    activateScene,
    arm,
    disarm,
  };
});
