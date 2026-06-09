import { describe, it, expect, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';
import { createI18n } from 'vue-i18n';
import de from '../locales/de.json';
import en from '../locales/en.json';

/**
 * app/AppShell — the Ionic application shell (A3, Issue #99).
 *
 * Covers the acceptance criteria: ion-app shell + a nav list from the host shell
 * state (in source order = the floor), the five shell-slots with host defaults
 * (header/roomDivider/background/empty/error), the optional titlebar, and the
 * room-grouped overview signal. The skin owns no state — the shell does.
 *
 * Ionic web components are not jsdom-friendly, so they are stubbed to plain
 * elements that still render their slots; menuController is mocked.
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
    IonButtons: passthrough('ion-buttons'),
    IonMenuButton: passthrough('ion-menu-button'),
    menuController: { close: vi.fn().mockResolvedValue(undefined) },
  };
});

import AppShell from './AppShell.vue';
import { NAV_KEYS } from './shell/useShellState';

function makeI18n(locale = 'de') {
  return createI18n({ legacy: false, locale, fallbackLocale: 'de', messages: { de, en } });
}

function mountShell(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  return mount(AppShell, { props, slots, global: { plugins: [makeI18n()] } });
}

describe('AppShell — shell + navigation', () => {
  it('renders an ion-app root', () => {
    const w = mountShell();
    expect(w.find('ion-app').exists()).toBe(true);
  });

  it('renders one nav item per section, in source order (the floor)', () => {
    const w = mountShell();
    const labels = w.findAll('ion-menu ion-item ion-label').map((n) => n.text());
    expect(labels).toHaveLength(NAV_KEYS.length);
    // first/last preserve source order; labels are localised
    expect(labels[0]).toBe(de.shell.nav.overview);
    expect(labels[labels.length - 1]).toBe(de.shell.nav.settings);
  });
});

describe('AppShell — shell slots have host defaults', () => {
  it('fills the header slot with the default ShellHeader (menu + title + clock)', () => {
    const w = mountShell();
    // default header (no titlebar) carries the active title and the clock pill
    expect(w.find('.shell-header').exists()).toBe(true);
    expect(w.find('.shell-header-title').text()).toBe(de.shell.nav.overview);
    expect(w.find('.clock-pill').exists()).toBe(true);
  });

  it('fills the background slot with the default decorative layer', () => {
    const w = mountShell();
    expect(w.find('.shell-background').exists()).toBe(true);
    expect(w.find('.shell-background').attributes('aria-hidden')).toBe('true');
  });

  it('shows the empty slot default when `empty` is set', () => {
    const w = mountShell({ empty: true });
    expect(w.find('.shell-empty').exists()).toBe(true);
    expect(w.find('.shell-empty-text').text()).toBe(de.shell.empty);
  });

  it('shows the error slot default (loud, not silent) when `error` is set', () => {
    const w = mountShell({ error: 'unknown skin "ghost"' });
    const err = w.find('.shell-error');
    expect(err.exists()).toBe(true);
    expect(err.attributes('role')).toBe('alert');
    expect(w.find('.shell-error-detail').text()).toContain('ghost');
  });

  it('lets a skin override a slot (header slot wins over the default)', () => {
    const w = mountShell({}, { header: '<div class="skin-header">SKIN</div>' });
    expect(w.find('.skin-header').exists()).toBe(true);
    expect(w.find('.shell-header').exists()).toBe(false);
  });
});

describe('AppShell — must-keep behaviours', () => {
  it('pulses the clock pill when unread', () => {
    const w = mountShell({ state: { unread: true } });
    expect(w.find('.clock-pill.unread').exists()).toBe(true);
  });

  it('marks messages read when the pill is tapped (host owns unread)', async () => {
    const w = mountShell({ state: { unread: true } });
    expect(w.find('.clock-pill.unread').exists()).toBe(true);
    await w.find('.clock-pill').trigger('click');
    expect(w.find('.clock-pill.unread').exists()).toBe(false);
  });

  it('optional titlebar: hidden by default, shown with showTitlebar', () => {
    expect(mountShell().find('.app-shell-titlebar').exists()).toBe(false);
    const w = mountShell({ state: { showTitlebar: true } });
    expect(w.find('.app-shell-titlebar').exists()).toBe(true);
    // brand label localised
    expect(w.find('.app-shell-titlebar').text()).toContain(de.shell.titlebar.brand);
  });

  it('exposes the RoomDivider component to the default slot (room-grouped overview)', () => {
    const w = mountShell(
      {},
      {
        default: `<template #default="{ roomDivider }"><component :is="roomDivider" room="Küche" :count="4" /></template>`,
      },
    );
    expect(w.find('.room-divider').exists()).toBe(true);
    expect(w.find('.room-divider-name').text()).toBe('Küche');
    expect(w.find('.room-divider-meta').text()).toBe('4');
  });
});
