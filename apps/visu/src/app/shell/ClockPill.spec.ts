import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import { createI18n } from 'vue-i18n';
import de from '../../locales/de.json';
import en from '../../locales/en.json';
import ClockPill from './ClockPill.vue';

/**
 * app/shell/ClockPill — date/time pill that doubles as the messages button
 * (A3 must-keep; port of VzClock). Pulses on unread; emits `read` on tap; the
 * aria-label switches with the unread state (AA).
 */
function makeI18n(locale = 'de') {
  return createI18n({ legacy: false, locale, fallbackLocale: 'de', messages: { de, en } });
}

function mountPill(props: Record<string, unknown> = {}, locale = 'de') {
  return mount(ClockPill, { props, global: { plugins: [makeI18n(locale)] } });
}

describe('ClockPill', () => {
  it('renders a date and a time', () => {
    const w = mountPill();
    expect(w.find('.clock-txt .d').exists()).toBe(true);
    expect(w.find('.clock-txt .t').exists()).toBe(true);
    expect(w.find('.clock-txt .d').text().length).toBeGreaterThan(0);
    expect(w.find('.clock-txt .t').text().length).toBeGreaterThan(0);
  });

  it('does not pulse / show the dot when there are no unread messages', () => {
    const w = mountPill({ unread: false });
    expect(w.find('.clock-pill').classes()).not.toContain('unread');
    expect(w.find('.clock-dot').exists()).toBe(false);
    // aria-label announces the calm state
    expect(w.find('.clock-pill').attributes('aria-label')).toBe(de.shell.clock.messages);
  });

  it('pulses (unread class) and shows the dot when unread, with the unread aria-label', () => {
    const w = mountPill({ unread: true });
    expect(w.find('.clock-pill').classes()).toContain('unread');
    expect(w.find('.clock-dot').exists()).toBe(true);
    expect(w.find('.clock-pill').attributes('aria-label')).toBe(de.shell.clock.unread);
  });

  it('emits `read` when tapped (the host clears unread — the pill owns no state)', async () => {
    const w = mountPill({ unread: true });
    await w.find('.clock-pill').trigger('click');
    expect(w.emitted('read')).toBeTruthy();
    expect(w.emitted('read')).toHaveLength(1);
  });
});
