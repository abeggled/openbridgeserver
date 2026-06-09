import { describe, it, expect } from 'vitest';

import { skins, resolveSkin } from './skins';
import { selectTile } from './dispatch';
import { byId } from '../core/model';

/**
 * skin-host/skins — registry + resolution (A1, Issue #97).
 *
 * The host is the only layer that imports a concrete skin; this verifies the
 * registry normalises `@obs-visu-skins/ionic` to the uniform Skin shape and that
 * a present core type dispatches to a real renderer through the registry.
 */

describe('skins registry', () => {
  it('registers the ionic skin with tiles/details/manifest', () => {
    expect(skins.ionic).toBeDefined();
    expect(skins.ionic.tiles).toBeTypeOf('object');
    expect(skins.ionic.details).toBeTypeOf('object');
    expect(skins.ionic.manifest.name).toBe('ionic');
  });

  it('resolveSkin returns the skin for a known key', () => {
    expect(resolveSkin('ionic')).toBe(skins.ionic);
  });

  it('resolveSkin throws a visible error for an unknown key (no silent default)', () => {
    expect(() => resolveSkin('does-not-exist')).toThrow(/unknown skin/i);
  });
});

describe('skins registry — present type renders through dispatch', () => {
  it('an ionic light tile renders for a real model device', () => {
    const sel = selectTile(skins.ionic.tiles, skins.ionic.manifest, 'light');
    expect(sel.renderer).not.toBeNull();
    // a representative light device from the model
    const light = byId['kueche-wand'];
    expect(light.type).toBe('light');
    // the renderer is callable and returns a node (markup or VNode)
    const out = sel.renderer!(light, fakeTokens, fakeCtx);
    expect(out).toBeDefined();
  });
});

/* minimal stand-ins so the renderer call does not depend on the full token/ctx
 * derivation — the dispatch + render seam is what this asserts. */
const fakeTokens = {
  accent: () => '#000',
  accentInk: () => '#fff',
  font: 'sans-serif',
  space: (n: number) => `${n * 4}px`,
};
const fakeCtx = {
  stateText: () => '',
  hyphenate: (t: string) => t,
  icon: () => '',
  nf: (v: number | string) => String(v),
  warn: () => false,
};
