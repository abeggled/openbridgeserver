/* eslint-disable vue/one-component-per-file -- test helpers (IonModal stub + a
   provide/inject capturing child) are intentionally co-located in this spec. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { mount, flushPromises } from '@vue/test-utils';
import { defineComponent, h, inject } from 'vue';
// (defineComponent/h used both for the capturing child and the IonModal stub below)
import type { Device } from '@obs/visu-contract';

import DetailModalHost, { HOST_KEY, type SkinHostApi } from './DetailModalHost.vue';
import { useDeviceStore } from '../core/store';
import { MockDataSource } from '../core/datasource';

/**
 * app/DetailModalHost — the host shell that owns the detail surface (A2, Issue #98).
 *
 * Golden rule 4: the skin owns no state. This host:
 *   - provides `dispatch(id, action, payload)` + `openDetail(id)` to descendants,
 *   - renders an ion-modal with the skin's `details[type]` renderer, or a generic
 *     DEFAULT-DETAIL (built from the model + the type's canonical actions) when the
 *     skin ships none,
 *   - captures the skin's `data-action` markers (tap + slider) and maps them to
 *     canonical core-store actions; `close` dismisses the modal.
 */

// jsdom doesn't define the ion-* custom elements; treat them as plain elements so
// mount() doesn't warn. The real @ionic/vue IonModal only renders its slotted body
// into the light DOM when actually *presented* (a web-component lifecycle that does
// not run under jsdom), so we stub it with a passthrough that reflects `is-open` and
// always renders its default slot — that lets us assert the host's body wiring.
const IonModalStub = defineComponent({
  name: 'IonModal',
  props: { isOpen: { type: Boolean, default: false } },
  setup(p, { slots }) {
    return () =>
      h('ion-modal', { 'is-open': String(!!p.isOpen) }, slots.default ? slots.default() : []);
  },
});

const global = {
  config: { compilerOptions: { isCustomElement: (tag: string) => tag.startsWith('ion-') } },
  stubs: { IonModal: IonModalStub },
};

async function seed(devices?: readonly Device[]): Promise<void> {
  const store = useDeviceStore();
  await store.init(devices ? new MockDataSource(devices) : new MockDataSource());
}

/** A child that grabs the provided host API so a test can drive it. */
function childCapturing(onApi: (api: SkinHostApi) => void) {
  return defineComponent({
    setup() {
      const api = inject<SkinHostApi>(HOST_KEY);
      if (api) onApi(api);
      return () => h('div', { class: 'child' });
    },
  });
}

describe('DetailModalHost — provides the host API', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('provides dispatch + openDetail + closeDetail to descendants', async () => {
    await seed();
    let api: SkinHostApi | undefined;
    mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    expect(typeof api?.dispatch).toBe('function');
    expect(typeof api?.openDetail).toBe('function');
    expect(typeof api?.closeDetail).toBe('function');
  });
});

describe('DetailModalHost — dispatch maps gestures to canonical store actions', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('forwards a toggle intent to the store action', async () => {
    await seed();
    const store = useDeviceStore();
    const spy = vi.spyOn(store, 'toggle');
    let api: SkinHostApi | undefined;
    mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.dispatch('kueche-wand', 'toggle');
    expect(spy).toHaveBeenCalledWith('kueche-wand');
  });

  it('forwards a setDim intent with a payload value', async () => {
    await seed();
    const store = useDeviceStore();
    const spy = vi.spyOn(store, 'setDim');
    let api: SkinHostApi | undefined;
    mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.dispatch('kueche-wand', 'setDim', 80);
    expect(spy).toHaveBeenCalledWith('kueche-wand', 80);
  });
});

describe('DetailModalHost — openDetail renders the skin detail surface', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('opens the modal and renders the skin details[type] for a light', async () => {
    await seed();
    let api: SkinHostApi | undefined;
    const wrapper = mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.openDetail('kueche-wand');
    await flushPromises();
    const modal = wrapper.find('ion-modal');
    expect(modal.attributes('is-open')).toBe('true');
    // the ionic LightDetail marks itself data-type="light"
    expect(wrapper.find('[data-type="light"]').exists()).toBe(true);
  });

  it('closeDetail / a close intent dismisses the modal', async () => {
    await seed();
    let api: SkinHostApi | undefined;
    const wrapper = mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.openDetail('kueche-wand');
    await flushPromises();
    expect(wrapper.find('ion-modal').attributes('is-open')).toBe('true');
    api!.closeDetail();
    await flushPromises();
    expect(wrapper.find('ion-modal').attributes('is-open')).toBe('false');
  });
});

describe('DetailModalHost — DEFAULT-DETAIL fallback', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('renders a generic detail (label + canonical action) when the skin ships none', async () => {
    // scene has no skin detail renderer (one-shot). The host must still open a
    // generic surface built from the model + its canonical action (activateScene).
    const scene = {
      type: 'scene', id: 'movie', room: 'EG Wohnz.', label: 'Filmabend', accent: 'violet',
      icon: 'scene', sub: 'Licht 20 % · Rollladen zu',
    } as Device;
    await seed([scene]);
    let api: SkinHostApi | undefined;
    const wrapper = mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.openDetail('movie');
    await flushPromises();

    const def = wrapper.find('.skin-host-default-detail');
    expect(def.exists()).toBe(true);
    // the label is shown
    expect(def.text()).toContain('Filmabend');
    // a canonical action control carries the data-action marker
    expect(wrapper.find('[data-action="activateScene"]').exists()).toBe(true);
  });

  it('clicking a DEFAULT-DETAIL action dispatches the canonical store action', async () => {
    const scene = {
      type: 'scene', id: 'movie', room: 'EG Wohnz.', label: 'Filmabend', accent: 'violet',
      icon: 'scene',
    } as Device;
    await seed([scene]);
    const store = useDeviceStore();
    const spy = vi.spyOn(store, 'activateScene');
    let api: SkinHostApi | undefined;
    const wrapper = mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.openDetail('movie');
    await flushPromises();
    await wrapper.find('[data-action="activateScene"]').trigger('click');
    expect(spy).toHaveBeenCalledWith('movie');
  });
});

describe('DetailModalHost — captured clicks inside the modal map to the store', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('a click on a detail control with data-action reaches the store', async () => {
    await seed();
    const store = useDeviceStore();
    const spy = vi.spyOn(store, 'setDim');
    let api: SkinHostApi | undefined;
    const wrapper = mount(DetailModalHost, {
      global,
      props: { skin: 'ionic' },
      slots: { default: () => h(childCapturing((a) => (api = a))) },
    });
    api!.openDetail('kueche-wand');
    await flushPromises();
    // LightDetail ships an "Aus" button → data-action=setDim data-value=0
    const off = wrapper.find('[data-action="setDim"][data-value="0"]');
    expect(off.exists()).toBe(true);
    await off.trigger('click');
    expect(spy).toHaveBeenCalledWith('kueche-wand', 0);
  });
});
