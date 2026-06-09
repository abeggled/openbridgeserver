import { describe, it, expect } from 'vitest';
import { useShellState, NAV_KEYS } from './useShellState';

/**
 * app/shell/useShellState — host-owned shell UI state (A3, Issue #99).
 *
 * The skin owns no state (Goldene Regel 1/4): the active nav, unread and
 * titlebar flags live here in the host. The nav list is the prototype's
 * top-level sections in source order (the floor, Goldene Regel 5).
 */
describe('useShellState', () => {
  it('exposes the nav sections in source order (store.js → NAV)', () => {
    const shell = useShellState();
    expect(shell.nav).toEqual(NAV_KEYS);
    // overview is first; settings last — order is the floor.
    expect(shell.nav[0]).toBe('overview');
    expect(shell.nav[shell.nav.length - 1]).toBe('settings');
  });

  it('defaults to the overview section, no unread, no titlebar', () => {
    const shell = useShellState();
    expect(shell.active.value).toBe('overview');
    expect(shell.unread.value).toBe(false);
    expect(shell.showTitlebar.value).toBe(false);
  });

  it('honours seed options', () => {
    const shell = useShellState({ active: 'energy', unread: true, showTitlebar: true });
    expect(shell.active.value).toBe('energy');
    expect(shell.unread.value).toBe(true);
    expect(shell.showTitlebar.value).toBe(true);
  });

  it('setNav switches the active section', () => {
    const shell = useShellState();
    shell.setNav('scenes');
    expect(shell.active.value).toBe('scenes');
  });

  it('markRead clears the unread pulse (store.js → open())', () => {
    const shell = useShellState({ unread: true });
    shell.markRead();
    expect(shell.unread.value).toBe(false);
  });

  it('setUnread feeds the unread flag (host/transport seam)', () => {
    const shell = useShellState();
    shell.setUnread(true);
    expect(shell.unread.value).toBe(true);
  });
});
