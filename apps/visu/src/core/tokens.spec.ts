import { describe, it, expect } from 'vitest';
import chroma from 'chroma-js';
import type { AccentToken } from '@obs/visu-contract';
import {
  PALETTE,
  SURFACE,
  AA,
  ink,
  readableOn,
  cssVars,
  makeTokens,
  THEMES,
  type Theme,
} from './tokens';

/**
 * core/tokens — chroma-js driven AA contrast + accent derivation (CO5, #95).
 *
 * Port of reference/vue-ionic/theme-chroma.js into the core, exposed as the
 * `Tokens` surface from CONTRACT-v1.md §5. Golden rules: one model/state lives
 * in core, renderers stay pure; AA (>= 4.5) is mandatory across every
 * palette × theme combination.
 */

const PALETTE_KEYS = Object.keys(PALETTE) as AccentToken[];

describe('core/tokens — constants', () => {
  it('AA threshold is the WCAG normal-text minimum (4.5)', () => {
    expect(AA).toBe(4.5);
  });

  it('PALETTE keys match the contract AccentToken union', () => {
    // The contract declares exactly these accent keys (store.js → ACCENTS).
    expect(PALETTE_KEYS.sort()).toEqual(
      ['amber', 'blue', 'green', 'orange', 'rose', 'slate', 'teal', 'violet'].sort(),
    );
  });

  it('exposes one surface per theme', () => {
    expect(THEMES).toEqual(['light', 'dark', 'image']);
    for (const theme of THEMES) {
      expect(typeof SURFACE[theme]).toBe('string');
    }
  });
});

describe('core/tokens — ink()', () => {
  it('returns a colour that itself clears AA on every palette swatch', () => {
    for (const key of PALETTE_KEYS) {
      const swatch = PALETTE[key];
      const fg = ink(swatch);
      expect(chroma.contrast(fg, swatch)).toBeGreaterThanOrEqual(AA);
    }
  });

  it('picks white ink on a dark swatch and dark ink on a light swatch', () => {
    expect(ink('#000000')).toBe('#ffffff');
    expect(ink('#ffffff')).not.toBe('#ffffff');
  });
});

describe('core/tokens — readableOn() clears AA across ALL palette × theme', () => {
  for (const theme of ['light', 'dark', 'image'] as Theme[]) {
    for (const key of [
      'orange',
      'teal',
      'violet',
      'green',
      'blue',
      'rose',
      'amber',
      'slate',
    ] as AccentToken[]) {
      it(`${key} on ${theme} surface is AA-safe`, () => {
        const surface = SURFACE[theme];
        const readable = readableOn(PALETTE[key], surface);
        expect(chroma.contrast(readable, surface)).toBeGreaterThanOrEqual(AA);
      });
    }
  }

  it('handles the extreme case: a colour equal to its surface still resolves to AA', () => {
    // Worst case for the nudge loop: zero starting contrast on a mid-grey image
    // surface — the derivation must still terminate above AA.
    const surface = SURFACE.image;
    const readable = readableOn(surface, surface);
    expect(chroma.contrast(readable, surface)).toBeGreaterThanOrEqual(AA);
  });

  it('extreme case on both light and dark surfaces also clears AA', () => {
    expect(chroma.contrast(readableOn(SURFACE.light, SURFACE.light), SURFACE.light)).toBeGreaterThanOrEqual(AA);
    expect(chroma.contrast(readableOn(SURFACE.dark, SURFACE.dark), SURFACE.dark)).toBeGreaterThanOrEqual(AA);
  });
});

describe('core/tokens — cssVars()', () => {
  it('emits per-palette ink + readable custom properties and accent props', () => {
    const vars = cssVars('light', 'orange');
    // every palette key gets an ink + readable var
    for (const key of PALETTE_KEYS) {
      expect(vars[`--vz-acc-${key}-ink`]).toBeDefined();
      expect(vars[`--vz-acc-${key}-readable`]).toBeDefined();
    }
    expect(vars['--vz-accent-ink']).toBeDefined();
    expect(vars['--vz-accent-readable']).toBeDefined();
    expect(vars['--vz-accent-shade']).toBeDefined();
    expect(vars['--vz-accent-tint']).toBeDefined();
    expect(vars['--vz-clock-ink']).toBeDefined();
  });

  it('accepts a palette KEY or a raw hex accent (theme-chroma.js parity)', () => {
    const byKey = cssVars('dark', 'teal');
    const byHex = cssVars('dark', PALETTE.teal);
    expect(byKey['--vz-accent-readable']).toBe(byHex['--vz-accent-readable']);
  });

  it('clock ink clears AA on every theme', () => {
    for (const theme of THEMES) {
      const vars = cssVars(theme, 'orange');
      expect(chroma.contrast(vars['--vz-clock-ink'], SURFACE[theme])).toBeGreaterThanOrEqual(AA);
    }
  });
});

describe('core/tokens — makeTokens() (CONTRACT-v1.md §5 Tokens interface)', () => {
  it('implements accent/accentInk/font/space', () => {
    const t = makeTokens('light', 'orange');
    expect(typeof t.accent('teal')).toBe('string');
    expect(typeof t.accentInk('teal')).toBe('string');
    expect(typeof t.font).toBe('string');
    expect(typeof t.space(2)).toBe('string');
  });

  it('accent() returns an AA-safe readable colour for the active theme', () => {
    for (const theme of THEMES) {
      const t = makeTokens(theme, 'orange');
      for (const key of PALETTE_KEYS) {
        expect(chroma.contrast(t.accent(key), SURFACE[theme])).toBeGreaterThanOrEqual(AA);
      }
    }
  });

  it('accentInk() returns ink that clears AA against the raw swatch', () => {
    const t = makeTokens('dark', 'orange');
    for (const key of PALETTE_KEYS) {
      expect(chroma.contrast(t.accentInk(key), PALETTE[key])).toBeGreaterThanOrEqual(AA);
    }
  });

  it('unknown accent token falls back instead of throwing', () => {
    const t = makeTokens('light', 'orange');
    expect(() => t.accent('does-not-exist')).not.toThrow();
    expect(typeof t.accent('does-not-exist')).toBe('string');
  });

  it('space() scales monotonically and returns a px length', () => {
    const t = makeTokens('light', 'orange');
    expect(t.space(0)).toMatch(/px$/);
    const a = parseFloat(t.space(1));
    const b = parseFloat(t.space(3));
    expect(b).toBeGreaterThan(a);
  });
});
