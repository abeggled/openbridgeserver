/**
 * apps/visu/scripts/m1-smoke.ts — M1 acceptance smoke (no UI).
 *
 * Wires the three M1 deliverables together exactly as a headless host would:
 *  1. the data/type **contract** (`@obs/visu-contract`),
 *  2. the austauschbare **MockDataSource** (core/datasource),
 *  3. the **Pinia host store** (core/store) that owns device state.
 *
 * It seeds the store from a MockDataSource (the real model devices plus one
 * scene, since the v1 mobile model carries no scene), drives the canonical
 * action sequence, and asserts the resulting end state:
 *
 *   • Licht aus → ein  ⇒ a light that is off with dim===0 jumps to dim 60 / on.
 *   • setPosition       ⇒ an out-of-range request is clamped to 0..100.
 *   • locked            ⇒ setPosition on a locked blind is blocked (no change).
 *   • activateScene     ⇒ a stateless scene intent dispatches without error.
 *
 * Runs under vitest (no browser, no Vue component mount) so it stays a pure
 * core-contract check and reuses the workspace's existing toolchain. Wired as
 * `pnpm m1:smoke`.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { Device, SceneDevice } from '@obs/visu-contract';
import { devices as modelDevices } from '@/core/model';
import { MockDataSource } from '@/core/datasource';
import { useDeviceStore } from '@/core/store';

/** The film scene from the contract fixtures — the v1 model has no scene device. */
const filmScene: SceneDevice = {
  id: 'szene-film',
  type: 'scene',
  room: 'EG Wohnz.',
  label: 'Filmabend',
  accent: 'violet',
  icon: 'play',
  sub: 'Licht gedimmt',
};

/** Seed = the real mobile model plus one scene, so activateScene has a target. */
const seed: readonly Device[] = [...modelDevices, filmScene];

describe('M1 smoke — contract + MockDataSource + Pinia store end state', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('drives the canonical sequence and lands in the expected end state', async () => {
    const store = useDeviceStore();
    await store.init(new MockDataSource(seed));

    // ── 1) Licht aus → ein ⇒ dim 60 ───────────────────────────────────────
    // kueche-pendel is a light, off, dim===0 → toggle must jump to dim 60 / on.
    const lightId = 'kueche-pendel';
    const before = store.byId(lightId);
    expect(before?.type).toBe('light');
    expect(before).toMatchObject({ on: false, dim: 0 });

    await store.toggle(lightId);

    const lit = store.byId(lightId);
    expect(lit).toMatchObject({ type: 'light', on: true, dim: 60 });

    // ── 2) setPosition clamps ─────────────────────────────────────────────
    // kueche-roll is an unlocked blind; an over-range request clamps to 100,
    // an under-range request clamps to 0.
    const blindId = 'kueche-roll';
    expect(store.byId(blindId)).toMatchObject({ type: 'blind', locked: false });

    await store.setPosition(blindId, 150);
    expect(store.byId(blindId)).toMatchObject({ position: 100 });

    await store.setPosition(blindId, -20);
    expect(store.byId(blindId)).toMatchObject({ position: 0 });

    // ── 3) locked blockiert die Kachel ────────────────────────────────────
    // wiga-roll is a locked blind; setPosition must be a no-op while locked.
    const lockedId = 'wiga-roll';
    const lockedBefore = store.byId(lockedId);
    expect(lockedBefore).toMatchObject({ type: 'blind', locked: true });
    const lockedPos = (lockedBefore as { position: number }).position;

    await store.setPosition(lockedId, 80);
    expect(store.byId(lockedId)).toMatchObject({ locked: true, position: lockedPos });

    // …and after unlock the same move now applies.
    await store.unlock(lockedId);
    await store.setPosition(lockedId, 80);
    expect(store.byId(lockedId)).toMatchObject({ locked: false, position: 80 });

    // ── 4) activateScene ──────────────────────────────────────────────────
    // Stateless intent — must dispatch without throwing.
    await expect(store.activateScene('szene-film')).resolves.toBeUndefined();

    // ── End-state assertion (single source of truth lives in the store) ────
    expect(store.byId('kueche-pendel')).toMatchObject({ on: true, dim: 60 });
    expect(store.byId('kueche-roll')).toMatchObject({ position: 0, locked: false });
    expect(store.byId('wiga-roll')).toMatchObject({ position: 80, locked: false });
  });
});
