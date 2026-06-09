/**
 * skin-host/actions — gestures → canonical core-store actions (A2, Issue #98).
 *
 * Golden rule 4: **the skin owns no state.** A skin renderer never mutates a
 * `Device` and never calls a store; it only *marks an intent* on a DOM node:
 *
 *   - `data-action`   — the action name (canonical, e.g. `toggle`/`setPosition`,
 *                       or a UI/host-level one: `stop`/`close`/`openDetail`).
 *   - `data-value`    — a numeric argument (LightDetail convention).
 *   - `data-arg`      — a numeric argument (Blind/Jalousie convention).
 *   - `data-relative` — present ⇒ the argument is a *delta* to apply to the live
 *                       value (the stepOpen/stepClose buttons), not an absolute.
 *
 * Sliders (`ion-range` / `input[type=range]`) carry their value on the event, not
 * a data attribute, so the host passes that value in alongside the parsed intent.
 *
 * This module is the single seam that turns such a marker into a canonical action
 * call on the core store ({@link dispatchIntent}). The store is addressed
 * structurally ({@link ActionStore}) so the mapping is a pure, Vue-free unit:
 * the host (SkinHost / DetailModalHost) wires a real `useDeviceStore()` into it.
 *
 * Quelle der Konventionen: reference/vue-ionic widgets.js + dialogs.js (the
 * prototype dispatched the same gestures inline; here they are lifted into the
 * host so skins stay stateless).
 */

import type { Device, WidgetAction } from '@obs/visu-contract';

/**
 * The canonical core actions plus the UI/host-level ones a skin may emit.
 *
 *  - canonical (forwarded to the store): the {@link WidgetAction} set.
 *  - `stop`        — a UI-only momentary control (no canonical core write in v1).
 *  - `close`       — close the detail surface (handled by the host shell).
 *  - `openDetail`  — open the detail surface (handled by the host shell).
 */
export type HostAction = WidgetAction | 'stop' | 'close' | 'openDetail';

/** Actions that carry no canonical core write — the host swallows them on the store. */
const UI_ONLY: ReadonlySet<HostAction> = new Set(['stop', 'close', 'openDetail']);

/** Actions handled by the host shell as a detail-surface open. */
const DETAIL_TRIGGERS: ReadonlySet<HostAction> = new Set(['openDetail']);

/** A UI-only action carries no canonical core write (e.g. a momentary `stop`). */
export function isUiOnly(action: HostAction): boolean {
  return UI_ONLY.has(action);
}

/** A detail trigger opens the detail surface rather than mutating state. */
export function isDetailTrigger(action: HostAction): boolean {
  return DETAIL_TRIGGERS.has(action);
}

/** A parsed skin intent: the action plus its optional (absolute|relative) argument. */
export interface Intent {
  readonly action: HostAction;
  /** Numeric argument from `data-value`/`data-arg`, if any. */
  readonly arg?: number;
  /** True when `arg` is a delta to apply to the device's live value. */
  readonly relative: boolean;
}

/**
 * The slice of the core device store the host calls. Declared structurally so the
 * mapping unit-tests against a spy, and so this module imports no skin and no
 * Pinia (boundaries: actions stays pure). Mirrors `useDeviceStore` (core/store).
 */
export interface ActionStore {
  toggle(id: string): unknown;
  setDim(id: string, pct: number): unknown;
  setPosition(id: string, pct: number): unknown;
  setSlat(id: string, pct: number): unknown;
  lock(id: string): unknown;
  unlock(id: string): unknown;
  activateScene(id: string): unknown;
  arm(id: string): unknown;
  disarm(id: string): unknown;
  /** Read a live device (to resolve relative deltas). Optional for callers that never step. */
  byId?(id: string): Device | undefined;
}

/** The known action names — used to validate a `data-action` string. */
const KNOWN_ACTIONS: ReadonlySet<string> = new Set<HostAction>([
  'toggle', 'setDim', 'setPosition', 'setSlat', 'lock', 'unlock',
  'activateScene', 'arm', 'disarm', 'stop', 'close', 'openDetail',
]);

/** Parse `data-arg`/`data-value` to a finite number, else undefined. */
function readArg(node: Element): number | undefined {
  const raw = node.getAttribute('data-value') ?? node.getAttribute('data-arg');
  if (raw == null || raw === '') return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

/**
 * Parse the skin intent off a triggering DOM node. The node itself need not carry
 * the marker — the nearest `[data-action]` ancestor is used (a click can land on a
 * label/icon inside the button). Returns `null` for a non-interactive node or an
 * unknown action string (never a silent guess — golden rule "no silent default").
 */
export function parseIntent(target: EventTarget | null): Intent | null {
  if (!(target instanceof Element)) return null;
  const node = target.closest('[data-action]');
  if (!node) return null;

  const action = node.getAttribute('data-action');
  if (!action || !KNOWN_ACTIONS.has(action)) return null;

  return {
    action: action as HostAction,
    arg: readArg(node),
    relative: node.getAttribute('data-relative') != null,
  };
}

/** Read the live device's current value for the field the action steps. */
function liveValue(store: ActionStore, id: string, action: HostAction): number | undefined {
  const d = store.byId?.(id);
  if (!d) return undefined;
  if (action === 'setSlat' && d.type === 'jalousie') return d.slat;
  if (action === 'setPosition' && (d.type === 'blind' || d.type === 'jalousie')) return d.position;
  if (action === 'setDim' && d.type === 'light') return d.dim ?? (d.on ? 100 : 0);
  return undefined;
}

/**
 * Resolve the numeric value for a value-carrying action:
 *  - relative + a resolvable live value → live + arg (a delta step),
 *  - else the explicit `arg` (absolute),
 *  - else the slider `eventValue` (ion-range / input[type=range]),
 *  - else undefined (nothing to set).
 */
function resolveValue(
  store: ActionStore,
  id: string,
  intent: Intent,
  eventValue?: number,
): number | undefined {
  if (intent.relative && intent.arg != null) {
    const base = liveValue(store, id, intent.action);
    return base != null ? base + intent.arg : intent.arg;
  }
  if (intent.arg != null) return intent.arg;
  return eventValue;
}

/**
 * Dispatch a parsed {@link Intent} for a device onto the core store. UI-only and
 * host-only actions (`stop`/`close`/`openDetail`) are intentionally swallowed
 * here — they carry no canonical core write; the host shell handles them
 * elsewhere. A value action with no resolvable value is ignored (nothing to set).
 *
 * @param eventValue  slider value for `ion-range`/`input[type=range]` inputs that
 *                    carry their value on the event rather than a data attribute.
 */
export function dispatchIntent(
  store: ActionStore,
  id: string,
  intent: Intent,
  eventValue?: number,
): void {
  switch (intent.action) {
    case 'toggle':
      store.toggle(id);
      return;
    case 'lock':
      store.lock(id);
      return;
    case 'unlock':
      store.unlock(id);
      return;
    case 'activateScene':
      store.activateScene(id);
      return;
    case 'arm':
      store.arm(id);
      return;
    case 'disarm':
      store.disarm(id);
      return;
    case 'setDim':
    case 'setPosition':
    case 'setSlat': {
      const value = resolveValue(store, id, intent, eventValue);
      if (value == null) return;
      if (intent.action === 'setDim') store.setDim(id, value);
      else if (intent.action === 'setPosition') store.setPosition(id, value);
      else store.setSlat(id, value);
      return;
    }
    // UI-only / host-only — no canonical core write (golden rule 4: state stays
    // in core; these are shell concerns the host handles, not store mutations).
    case 'stop':
    case 'close':
    case 'openDetail':
      return;
  }
}
