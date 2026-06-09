/**
 * useLongPress — long-press gesture composable.
 *
 * Ported 1:1 in behaviour from the prototype `useLongPress` in
 * reference/vue-ionic/store.js (see MIGRATION.md §2 / §7):
 *  - fires after 420 ms of sustained press,
 *  - aborts when the pointer moves more than 10 px in either axis,
 *  - cancels on pointerup / pointerleave,
 *  - suppresses the native context menu.
 *
 * The haptic feedback is encapsulated in {@link buzz} so the platform
 * implementation (today `navigator.vibrate`) can later be swapped for
 * `@capacitor/haptics` without touching the gesture logic (MIGRATION.md §7.4).
 *
 * Pure gesture/timer logic — owns no application state (Goldene Regel 4).
 */

export interface LongPressOptions {
  /** Press duration in milliseconds before the callback fires. Default 420. */
  ms?: number;
}

export interface LongPressHandlers {
  onPointerdown(e: PointerEvent): void;
  onPointermove(e: PointerEvent): void;
  onPointerup(): void;
  onPointerleave(): void;
  onContextmenu(e: Event): void;
  /** Whether the long-press fired for the current/last press cycle. */
  readonly fired: boolean;
}

/** Movement tolerance: a drag farther than this (px, either axis) cancels. */
const MOVE_TOLERANCE_PX = 10;
/** Default long-press threshold. */
const DEFAULT_MS = 420;

/** Encapsulated haptic feedback; swappable for @capacitor/haptics later. */
function buzz(): void {
  try {
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
      navigator.vibrate(8);
    }
  } catch {
    /* haptics are best-effort; never let them break the gesture */
  }
}

export function useLongPress(
  cb: (e: PointerEvent) => void,
  { ms = DEFAULT_MS }: LongPressOptions = {},
): LongPressHandlers {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let fired = false;
  let startX = 0;
  let startY = 0;

  const clear = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return {
    onPointerdown(e: PointerEvent): void {
      fired = false;
      startX = e.clientX;
      startY = e.clientY;
      clear();
      timer = setTimeout(() => {
        timer = null;
        fired = true;
        buzz();
        cb(e);
      }, ms);
    },
    onPointermove(e: PointerEvent): void {
      if (
        timer !== null &&
        (Math.abs(e.clientX - startX) > MOVE_TOLERANCE_PX ||
          Math.abs(e.clientY - startY) > MOVE_TOLERANCE_PX)
      ) {
        clear();
      }
    },
    onPointerup(): void {
      clear();
    },
    onPointerleave(): void {
      clear();
    },
    onContextmenu(e: Event): void {
      e.preventDefault();
    },
    get fired(): boolean {
      return fired;
    },
  };
}
