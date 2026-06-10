import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';
import { createI18n } from 'vue-i18n';
import { setActivePinia, createPinia } from 'pinia';

import de from '../locales/de.json';
import en from '../locales/en.json';
import { useDeviceStore } from '../core/store';
import { MockDataSource } from '../core/datasource';
import { rooms as modelRooms } from '../core/model';

/**
 * pages/OverviewPage — the room-grouped mobile overview (M2, the first Ionic page).
 *
 * The sichtbare M2-Deliverable: mounts without error, renders one tile per
 * `mobileGroups` item (order + grouping as the floor) through the ionic skin, and
 * carries the core widget types (light/blind/jalousie/sensor/scene/switch). Ionic
 * web components are not jsdom-friendly, so they are stubbed to plain elements
 * that still render their slots (same pattern as AppShell.spec).
 */
vi.mock('@ionic/vue', () => {
  const passthrough = (tag: string) =>
    defineComponent({
      name: tag,
      setup(_props, { slots }) {
        return () => h(tag, {}, slots.default ? slots.default() : []);
      },
    });
  return {
    IonApp: passthrough('ion-app'),
    IonContent: passthrough('ion-content'),
    IonHeader: passthrough('ion-header'),
    IonMenu: passthrough('ion-menu'),
    IonList: passthrough('ion-list'),
    IonItem: passthrough('ion-item'),
    IonLabel: passthrough('ion-label'),
    IonPage: passthrough('ion-page'),
    IonRouterOutlet: passthrough('ion-router-outlet'),
    IonToolbar: passthrough('ion-toolbar'),
    IonTitle: passthrough('ion-title'),
    IonModal: passthrough('ion-modal'),
    IonButtons: passthrough('ion-buttons'),
    IonMenuButton: passthrough('ion-menu-button'),
    menuController: { close: vi.fn().mockResolvedValue(undefined) },
  };
});

import OverviewPage from './OverviewPage.vue';

function makeI18n(locale = 'de') {
  return createI18n({ legacy: false, locale, fallbackLocale: 'de', messages: { de, en } });
}

/** Total devices referenced across the room groups (the floor item count). */
function totalGroupItems(): number {
  return modelRooms.reduce((n, g) => n + g.entries.length, 0);
}

async function seedStore(): Promise<void> {
  const store = useDeviceStore();
  await store.init(new MockDataSource());
}

async function mountOverview() {
  const wrapper = mount(OverviewPage, { global: { plugins: [makeI18n()] } });
  await wrapper.vm.$nextTick();
  return wrapper;
}

describe('OverviewPage — mounts and renders the room-grouped overview', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('mounts without error', async () => {
    await seedStore();
    const wrapper = await mountOverview();
    expect(wrapper.exists()).toBe(true);
    // chrome present: the host shell + the skin grid
    expect(wrapper.find('ion-app').exists()).toBe(true);
    expect(wrapper.find('.skin-host').exists()).toBe(true);
  });

  it('renders one tile per mobileGroups item (order + grouping as the floor)', async () => {
    await seedStore();
    const wrapper = await mountOverview();
    const cells = wrapper.findAll('.skin-host-cell');
    expect(cells.length).toBe(totalGroupItems());
    expect(cells.length).toBeGreaterThanOrEqual(modelRooms.length);
  });

  it('renders one grid per room block (grouping is part of the floor)', async () => {
    await seedStore();
    const wrapper = await mountOverview();
    // Each room block is its own grid (clean rows + room separation).
    const roomGrids = wrapper.findAll('.skin-host-model-grid');
    expect(roomGrids.length).toBe(modelRooms.length);
    expect(roomGrids[0].attributes('data-group')).toBe(modelRooms[0].room);
    // first cell belongs to the first room block, in source order
    const first = wrapper.find('.skin-host-cell');
    expect(first.attributes('data-group')).toBe(modelRooms[0].room);
  });

  it('renders the core mobile widget types via the ionic skin (type-addressed)', async () => {
    await seedStore();
    const wrapper = await mountOverview();
    // The v1 mobile model (mobileGroups) carries light · switch · blind · jalousie.
    // Each is rendered by the ionic skin's type-addressed renderer; assert the
    // skin's per-type markup is present (a missing type would have thrown a gap).
    expect(wrapper.find('.vz-tile[data-type="light"]').exists()).toBe(true); // light
    expect(wrapper.find('.vz-tile[data-type="switch"]').exists()).toBe(true); // switch
    expect(wrapper.find('.vz-tile.blind').exists()).toBe(true); // blind
    expect(wrapper.find('.jal-body').exists()).toBe(true); // jalousie
  });

  it('tapping a tile dispatches its canonical action to the store (data-id wiring)', async () => {
    // Regression: the host resolves the tapped tile's device id from the cell's
    // data-id (OverviewGrid → tileIdFor → cell.dataset.id). When that attribute
    // was missing, every tap resolved no id and silently no-op'd — taps did
    // nothing although the markup rendered fine. Guard the full gesture → store path.
    await seedStore();
    const store = useDeviceStore();
    const wrapper = await mountOverview();

    const id = 'kueche-wand'; // first mobile light (Wandleuchten), starts off
    const lightOn = () => (store.byId(id) as { on?: boolean } | undefined)?.on;
    expect(lightOn()).toBe(false);

    // every cell must carry its device id for the gesture mapping to work
    const cell = wrapper.findAll('.skin-host-cell').find((c) => c.attributes('data-id') === id);
    expect(cell).toBeDefined();

    // tap the light tile → the host dispatches `toggle` onto the core store
    await cell!.find('[data-type="light"]').trigger('click');
    expect(lightOn()).toBe(true);
  });

  it('exposes the ionic tweak controls when the panel is opened (A6, page owns values)', async () => {
    await seedStore();
    const wrapper = await mountOverview();
    expect(wrapper.find('.tweaks-panel').exists()).toBe(false);
    await wrapper.find('.overview-tweaks-toggle').trigger('click');
    expect(wrapper.find('.tweaks-panel').exists()).toBe(true);
    // the panel renders the ionic skin's declared tweaks (stil/accentStyle/…)
    expect(wrapper.find('[data-tweak="stil"]').exists()).toBe(true);
  });
});
