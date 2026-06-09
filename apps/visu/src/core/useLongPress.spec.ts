import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useLongPress } from './useLongPress';

/**
 * Long-press composable contract (port of the prototype `useLongPress` in
 * reference/vue-ionic/store.js, see MIGRATION.md §2 / §7):
 *  - fires after 420 ms of sustained press
 *  - cancels on pointerup / pointerleave before the threshold
 *  - aborts if the pointer moves more than 10 px in either axis
 *  - suppresses the native context menu
 *  - emits a haptic buzz when it fires (encapsulated, today navigator.vibrate)
 */

function down(x = 0, y = 0): PointerEvent {
  return { clientX: x, clientY: y } as unknown as PointerEvent;
}

describe('useLongPress', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('fires the callback after 420 ms of sustained press', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down());
    expect(cb).not.toHaveBeenCalled();
    expect(lp.fired).toBe(false);

    vi.advanceTimersByTime(419);
    expect(cb).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(cb).toHaveBeenCalledTimes(1);
    expect(lp.fired).toBe(true);
  });

  it('honours a custom threshold via the ms option', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb, { ms: 1000 });

    lp.onPointerdown(down());
    vi.advanceTimersByTime(420);
    expect(cb).not.toHaveBeenCalled();

    vi.advanceTimersByTime(580);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('cancels on pointerup before the threshold', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down());
    vi.advanceTimersByTime(200);
    lp.onPointerup();

    vi.advanceTimersByTime(1000);
    expect(cb).not.toHaveBeenCalled();
    expect(lp.fired).toBe(false);
  });

  it('cancels on pointerleave before the threshold', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down());
    vi.advanceTimersByTime(200);
    lp.onPointerleave();

    vi.advanceTimersByTime(1000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('aborts when the pointer moves more than 10 px on the x axis', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down(0, 0));
    lp.onPointermove(down(11, 0));

    vi.advanceTimersByTime(1000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('aborts when the pointer moves more than 10 px on the y axis', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down(0, 0));
    lp.onPointermove(down(0, 11));

    vi.advanceTimersByTime(1000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('tolerates small movements within the 10 px threshold', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down(0, 0));
    lp.onPointermove(down(10, 10));

    vi.advanceTimersByTime(420);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('suppresses the native context menu', () => {
    const lp = useLongPress(vi.fn());
    const preventDefault = vi.fn();

    lp.onContextmenu({ preventDefault } as unknown as Event);
    expect(preventDefault).toHaveBeenCalledTimes(1);
  });

  it('emits a haptic buzz when it fires', () => {
    const vibrate = vi.fn();
    (navigator as unknown as { vibrate: (p: number) => void }).vibrate = vibrate;

    const lp = useLongPress(vi.fn());
    lp.onPointerdown(down());
    vi.advanceTimersByTime(420);

    expect(vibrate).toHaveBeenCalledTimes(1);
  });

  it('resets the fired flag on a fresh pointerdown', () => {
    const cb = vi.fn();
    const lp = useLongPress(cb);

    lp.onPointerdown(down());
    vi.advanceTimersByTime(420);
    expect(lp.fired).toBe(true);

    lp.onPointerdown(down());
    expect(lp.fired).toBe(false);
  });
});
