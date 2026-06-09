/**
 * app/shell/useShellState — host-owned shell UI state (A3, Issue #99).
 *
 * Goldene Regel 1/4: **the skin owns no state.** The shell chrome — which
 * navigation entry is active, whether there are unread messages, whether the
 * optional titlebar shows — is *host* UI state, not skin state. A skin fills the
 * shell's slots with markup but reads this state through props; it never holds
 * it. This composable is that single host-side owner for the AppShell.
 *
 * The navigation list is the prototype's top-level sections
 * (`reference/vue-ionic/store.js → NAV`): a flat, ordered list of section
 * labels. Order is the floor (Goldene Regel 5) — entries render in source order.
 * Labels are i18n keys resolved at the call site so the nav re-localises when the
 * locale changes; the raw key list lives here.
 *
 * Data and behaviour kept apart (Daten=JSON, Verhalten=Code): the nav key list is
 * plain data, the only code here is the reactive state + the canonical setters
 * the host calls (the shell maps a tap to `setNav` / `markRead`, never the skin).
 */

import { ref, type Ref } from 'vue';

/**
 * The top-level navigation sections, in source order (store.js → NAV). Each is
 * an i18n key under `shell.nav.*`; the shell resolves them with `t()` so labels
 * track the active locale.
 */
export const NAV_KEYS = [
  'overview',
  'groundFloor',
  'upperFloor',
  'basement',
  'garden',
  'security',
  'energy',
  'scenes',
  'settings',
] as const;

/** A navigation section key (one of {@link NAV_KEYS}). */
export type NavKey = (typeof NAV_KEYS)[number];

/** The reactive shell state surface the AppShell binds against. */
export interface ShellState {
  /** All navigation section keys, in source order (the floor). */
  readonly nav: readonly NavKey[];
  /** The active navigation section. */
  readonly active: Ref<NavKey>;
  /** Whether there are unread messages — drives the clock pill's pulse. */
  readonly unread: Ref<boolean>;
  /** Whether the optional brand titlebar is shown. */
  readonly showTitlebar: Ref<boolean>;
  /** Select a navigation section (canonical host action; the skin only emits intent). */
  setNav(key: NavKey): void;
  /** Mark messages read — clears the pill's unread pulse (store.js → open()). */
  markRead(): void;
  /** Set unread state (host/transport feeds this; M2 keeps it local). */
  setUnread(value: boolean): void;
}

/** Options to seed the shell state (tests pass overrides; the app keeps defaults). */
export interface ShellStateOptions {
  readonly active?: NavKey;
  readonly unread?: boolean;
  readonly showTitlebar?: boolean;
}

/**
 * Build the host-owned shell state. Plain composable (no Pinia): this is view
 * chrome, not device state — the device store stays the single owner of device
 * state, this owns only the shell's own UI flags.
 */
export function useShellState(options: ShellStateOptions = {}): ShellState {
  const active = ref<NavKey>(options.active ?? 'overview');
  const unread = ref<boolean>(options.unread ?? false);
  const showTitlebar = ref<boolean>(options.showTitlebar ?? false);

  function setNav(key: NavKey): void {
    active.value = key;
  }

  function markRead(): void {
    unread.value = false;
  }

  function setUnread(value: boolean): void {
    unread.value = value;
  }

  return {
    nav: NAV_KEYS,
    active,
    unread,
    showTitlebar,
    setNav,
    markRead,
    setUnread,
  };
}
