/**
 * skin-host/SkinHost — the host render component (A1 + A4, Issues #97 + #100).
 *
 * This is the single place the app turns a page's `skin` key + ordered, grouped
 * items into rendered tiles. It wires the three host responsibilities together:
 *
 *   1. resolve the skin from the registry         (skins.ts)
 *   2. resolve the layout from the manifest        (layout.ts — A4)
 *   3. dispatch each item to its type's renderer    (dispatch.ts — A1)
 *
 * Goldene Regeln honoured:
 *   - The skin owns no state: the host reads live device state from the core
 *     store and hands each renderer the read-only `(device, tokens, ctx)` triple
 *     (golden rule 1/4). Gestures are the host's job (a later workstream); this
 *     component is pure render dispatch + layout.
 *   - Renderer addressed by type; a missing, non-unsupported type throws a gap
 *     (golden rule 2/3) — surfaced loudly, never a silent default lamp.
 *   - Order + grouping are the floor; role/span are additive (golden rule 5):
 *     the layout drives the item order and the grid footprint per item.
 *   - AA tokens come from the core `makeTokens` (golden rule 6).
 *
 * Implemented as a `defineComponent` render function (not an SFC) because the
 * skin renderers already return VNodes — a render function composes them
 * directly and stays trivially unit-testable.
 */

import { defineComponent, h, computed, type PropType, type VNode } from 'vue';

import type { Device } from '@obs/visu-contract';
import { makeTokens, type Theme } from '../core/tokens';
import { ctx as defaultCtx } from '../core/ctx';
import { useDeviceStore } from '../core/store';
import { rooms as modelRooms, type RoomGroup } from '../core/model';

import { resolveSkin } from './skins';
import { resolveLayout, clampColumns } from './layout';
import { selectTile } from './dispatch';

export default defineComponent({
  name: 'SkinHost',
  props: {
    /** The page's chosen skin key (author's decision — no runtime switch). */
    skin: { type: String, required: true },
    /** The ordered, grouped room blocks to render (defaults to the core model). */
    groups: {
      type: Array as PropType<readonly RoomGroup[]>,
      default: () => modelRooms,
    },
    /** Active theme for AA-safe tokens. */
    theme: { type: String as PropType<Theme>, default: 'light' },
    /** Requested column count (clamped into the skin's declared window). */
    columns: { type: Number, default: undefined },
  },
  setup(props) {
    const store = useDeviceStore();

    /** Live device for an id: store state (the host owns state), else undefined. */
    function liveDevice(id: string): Device | undefined {
      return store.byId(id);
    }

    const skin = computed(() => resolveSkin(props.skin));
    const layout = computed(() =>
      resolveLayout(skin.value.manifest.layout, props.groups, liveDevice),
    );

    const cols = computed(() =>
      clampColumns(layout.value.columns, props.columns ?? layout.value.columns.default),
    );

    return () => {
      const sk = skin.value;
      const lay = layout.value;

      // Render one cell: type-addressed dispatch + AA tokens + the role/group
      // data the skin's CSS honours. Order is the array order (the floor).
      const renderCell = (item: (typeof lay.items)[number]): VNode => {
        // resolveLayout already proved the device exists; re-read it for render.
        const device = liveDevice(item.id) as Device;

        // A1: type-addressed dispatch. Throws a gap for an undeclared type.
        const selection = selectTile(sk.tiles, sk.manifest, device.type);

        // AA tokens + the renderer sandbox ctx (golden rules 4/6).
        const tokens = makeTokens(props.theme, device.accent);

        // A declared-unsupported type renders a quiet, labelled placeholder
        // (a declared gap, not a crash — golden rule 3).
        const body =
          selection.renderer === null
            ? h('div', { class: 'skin-host-unsupported', 'data-type': device.type }, '')
            : (selection.renderer(device, tokens, defaultCtx) as VNode);

        return h(
          'div',
          {
            key: item.id,
            class: 'skin-host-cell',
            // The host resolves the device id of a tapped tile from the cell
            // (OverviewGrid → tileIdFor → cell.dataset.id), so the gesture maps to
            // a canonical action. Without it, every tap resolves no id → no-op.
            'data-id': item.id,
            'data-group': item.group,
            'data-role': item.role,
            // Grid footprint: only meaningful in a role-honouring grid model.
            style: lay.honorsRole
              ? { gridColumn: `span ${item.span.c}`, gridRow: `span ${item.span.r}` }
              : undefined,
          },
          [body],
        );
      };

      // List model (e.g. terminal): one flat column.
      if (lay.model !== 'grid') {
        return h('div', { class: ['skin-host', 'skin-host-model-list'] }, lay.items.map(renderCell));
      }

      // Grid model: one grid PER room block so each room lays out cleanly (uniform
      // rows within a block) and reads as a separate room via the gap between
      // blocks (A4 — order + grouping are the floor). The clamped column count is
      // exposed once on the host and inherited by every room grid.
      const blocks: { group: string; items: (typeof lay.items)[number][] }[] = [];
      for (const item of lay.items) {
        const last = blocks[blocks.length - 1];
        if (last && last.group === item.group) last.items.push(item);
        else blocks.push({ group: item.group, items: [item] });
      }

      return h(
        'div',
        {
          class: ['skin-host', 'skin-host-grouped'],
          style: { '--skin-host-columns': String(cols.value) },
        },
        blocks.map((blk) =>
          h(
            'div',
            { key: `grid-${blk.group}`, class: 'skin-host-model-grid', 'data-group': blk.group },
            blk.items.map(renderCell),
          ),
        ),
      );
    };
  },
});
