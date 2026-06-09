<script lang="ts">
/**
 * app/DetailModalHost — the host shell that owns the detail surface (A2, Issue #98).
 *
 * Golden rule 4: **the skin owns no state — the host maps gestures to canonical
 * core-store actions.** This component is the place that wiring lives:
 *
 *   1. It `provide`s a {@link SkinHostApi} to descendants (`dispatch` · `openDetail`
 *      · `closeDetail`). Tiles/details mark intents with `data-action`; the host
 *      turns them into canonical store calls via skin-host/actions.
 *   2. A long-press on a tile (core/useLongPress, wired by the tile host) calls
 *      `openDetail(id)`, which opens an `ion-modal`.
 *   3. The modal renders the skin's `details[type]` renderer, or — when the skin
 *      ships none — a generic DEFAULT-DETAIL built from the core model + the
 *      type's canonical actions (so a one-shot `scene` or a read-only `sensor`
 *      still has a surface, and the same `data-action` capture path drives it).
 *   4. A `click`/`input` inside the modal is captured at the modal root, parsed
 *      into an {@link Intent} (skin-host/actions) and dispatched to the store; a
 *      `close` intent dismisses the modal.
 *
 * Quelle: reference/vue-ionic app.js (openDialog/ion-modal) + dialogs.js. There the
 * dialog mutated the device directly; here every control is a host intent so the
 * skin stays stateless and the store remains the single owner of state.
 */
import {
  defineComponent,
  h,
  computed,
  ref,
  provide,
  type InjectionKey,
  type PropType,
  type VNode,
} from 'vue';
import { IonModal } from '@ionic/vue';

import type { Device } from '@obs/visu-contract';
import { useDeviceStore } from '../core/store';
import { ctx as defaultCtx } from '../core/ctx';
import { makeTokens, type Theme } from '../core/tokens';
import { resolveSkin } from '../skin-host/skins';
import {
  parseIntent,
  dispatchIntent,
  type HostAction,
  type ActionStore,
} from '../skin-host/actions';

/** The host surface provided to descendants (tiles drive it via these handles). */
export interface SkinHostApi {
  /** Map a skin gesture to a canonical core-store action (golden rule 4). */
  dispatch(id: string, action: HostAction, payload?: number): void;
  /** Open the detail surface for a device id (long-press / `openDetail` intent). */
  openDetail(id: string): void;
  /** Dismiss the detail surface. */
  closeDetail(): void;
}

/** Inject key for the host API. */
export const HOST_KEY: InjectionKey<SkinHostApi> = Symbol('obs-visu-skin-host');

/** Slider value off an `ion-range`/`input[type=range]` event, else undefined. */
function eventSliderValue(ev: Event): number | undefined {
  const t = ev.target as (HTMLInputElement & { value?: unknown }) | null;
  // ion-range carries the value on event.detail.value; input[type=range] on .value.
  const detail = (ev as CustomEvent<{ value?: unknown }>).detail;
  const raw = detail?.value ?? t?.value;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

export default defineComponent({
  name: 'DetailModalHost',
  props: {
    /** The page's chosen skin key (author's decision — no runtime switch). */
    skin: { type: String, required: true },
    /** Active theme for AA-safe tokens in the detail surface. */
    theme: { type: String as PropType<Theme>, default: 'light' },
  },
  setup(props, { slots }) {
    const store = useDeviceStore();
    const skin = computed(() => resolveSkin(props.skin));

    /** The device whose detail is open, or null when the modal is closed. */
    const openId = ref<string | null>(null);
    const isOpen = computed(() => openId.value !== null);
    const device = computed<Device | undefined>(() =>
      openId.value !== null ? store.byId(openId.value) : undefined,
    );

    // The store, narrowed to the action surface actions.ts calls (incl. byId for
    // resolving relative slider steps). `store` already satisfies this shape.
    const actionStore: ActionStore = store as unknown as ActionStore;

    function dispatch(id: string, action: HostAction, payload?: number): void {
      // openDetail is a shell concern, not a store write — handle it here.
      if (action === 'openDetail') {
        openDetail(id);
        return;
      }
      dispatchIntent(actionStore, id, { action, arg: payload, relative: false }, payload);
    }

    function openDetail(id: string): void {
      openId.value = id;
    }
    function closeDetail(): void {
      openId.value = null;
    }

    provide<SkinHostApi>(HOST_KEY, { dispatch, openDetail, closeDetail });

    /**
     * Capture a tap/input inside the modal: parse the skin's data-action marker
     * and dispatch the canonical action. `close` dismisses the modal; UI-only and
     * unknown nodes are ignored (no silent state write — golden rule 4).
     */
    function onModalEvent(ev: Event): void {
      const id = openId.value;
      if (id === null) return;
      const intent = parseIntent(ev.target);
      if (!intent) return;
      if (intent.action === 'close') {
        closeDetail();
        return;
      }
      dispatchIntent(actionStore, id, intent, eventSliderValue(ev));
    }

    /**
     * The generic DEFAULT-DETAIL — used when the skin ships no `details[type]`.
     * Built from the core model (label/room/state) + the type's canonical actions,
     * marking each control with the same `data-action` the host already captures.
     * Read-only types (sensor) get a state line and no action control.
     */
    function defaultDetail(d: Device): VNode {
      // The generic surface uses the centralised `ctx.t` (with the same German
      // fallback the skin detail renderers use) so it tracks the active locale
      // when the host injected a translator, and reads sensibly without one.
      const tr = (key: string, fallback: string): string =>
        defaultCtx.t ? defaultCtx.t(key) : fallback;

      const actions: VNode[] = [];
      if (d.type === 'scene') {
        actions.push(
          h(
            'button',
            { class: 'skin-host-default-action', type: 'button', 'data-action': 'activateScene' },
            tr('skin.default.activate', 'Aktivieren'),
          ),
        );
      } else if (d.type === 'light' || d.type === 'switch') {
        actions.push(
          h(
            'button',
            { class: 'skin-host-default-action', type: 'button', 'data-action': 'toggle' },
            tr('skin.default.toggle', 'Schalten'),
          ),
        );
      }
      // blind/jalousie/sensor: state line only in the generic surface (the rich
      // controls live in the skin's own detail; the fallback stays minimal).

      return h('div', { class: 'skin-host-default-detail', 'data-type': d.type }, [
        h('div', { class: 'skin-host-default-crumb' }, d.room),
        h('h2', { class: 'skin-host-default-title' }, d.label),
        h('div', { class: 'skin-host-default-state' }, defaultCtx.stateText(d)),
        actions.length ? h('div', { class: 'skin-host-default-actions' }, actions) : null,
      ]);
    }

    /** The detail body: the skin's renderer, or the generic fallback. */
    const detailBody = computed<VNode | null>(() => {
      const d = device.value;
      if (!d) return null;
      const renderer = (skin.value.details as Record<string, ((d: Device, t: ReturnType<typeof makeTokens>, c: typeof defaultCtx) => unknown) | undefined>)[d.type];
      if (renderer) {
        const tokens = makeTokens(props.theme, d.accent);
        return renderer(d, tokens, defaultCtx) as VNode;
      }
      return defaultDetail(d);
    });

    return () =>
      h('div', { class: 'skin-host-detail-shell' }, [
        // Descendants (the tile host / pages) render in the default slot and reach
        // the host API through provide/inject.
        slots.default ? slots.default() : null,
        h(
          IonModal,
          {
            class: 'skin-host-modal',
            'is-open': isOpen.value,
            onDidDismiss: closeDetail,
          },
          {
            default: () =>
              detailBody.value
                ? h(
                    'div',
                    {
                      class: 'skin-host-modal-body',
                      // Single capture seam: every control inside marks an intent;
                      // the host owns the mapping to canonical store actions.
                      onClick: onModalEvent,
                      onInput: onModalEvent,
                    },
                    [detailBody.value],
                  )
                : null,
          },
        ),
      ]);
  },
});
</script>
