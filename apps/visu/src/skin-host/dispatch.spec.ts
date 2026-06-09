import { describe, it, expect } from 'vitest';
import type { Renderer, SkinManifest } from '@obs/visu-contract';

import { selectTile } from './dispatch';
import type { RendererMap } from './skins';

/**
 * skin-host/dispatch — type-addressed renderer selection (A1, Issue #97).
 *
 * Golden rule 2: renderer addressed by type, no silent switch default.
 * Golden rule 3: "not supported" is declared, never forgotten — an undeclared
 * missing type is a hard, visible gap.
 */

const stubRenderer: Renderer = () => 'tile';

const tiles: RendererMap = { light: stubRenderer };

const manifest = {
  name: 'test-skin',
  targetsContract: '1.1',
  unsupported: ['camera', 'media'],
  widgets: { light: { actions: ['toggle'] } },
  layout: { model: 'grid' },
} as unknown as SkinManifest;

describe('selectTile — present type', () => {
  it('returns the renderer for a type the skin ships', () => {
    const sel = selectTile(tiles, manifest, 'light');
    expect(sel.renderer).toBe(stubRenderer);
    expect(sel.unsupported).toBe(false);
  });
});

describe('selectTile — declared unsupported', () => {
  it('returns a null renderer (deliberate skip), not a throw', () => {
    const sel = selectTile(tiles, manifest, 'camera');
    expect(sel.renderer).toBeNull();
    expect(sel.unsupported).toBe(true);
  });
});

describe('selectTile — gap (missing + not unsupported)', () => {
  it('throws a visible gap error — never a silent default', () => {
    expect(() => selectTile(tiles, manifest, 'switch')).toThrow(/gap/i);
  });

  it('names the offending type and the skin in the error', () => {
    expect(() => selectTile(tiles, manifest, 'blind')).toThrow(/blind/);
    expect(() => selectTile(tiles, manifest, 'blind')).toThrow(/test-skin/);
  });
});
