// core/tokens — chroma-js driven AA contrast + accent derivation (CO5, #95).
//
// Port of reference/vue-ionic/theme-chroma.js into the Visu core. It computes,
// per theme, contrast-safe inks and readable accents from the fixed PALETTE and
// exposes them both as CSS custom properties (cssVars) and as the `Tokens`
// surface that renderers receive (CONTRACT-v1.md §5).
//
// Golden rules: the one model/state lives in core and renderers stay pure — this
// module derives colours, it never reads or mutates device state. AA (>= 4.5)
// is mandatory across every palette × theme combination; the derivation nudges
// a swatch until it clears that threshold against the active surface.

import chroma from 'chroma-js';
import type { AccentToken, Tokens } from '@obs/visu-contract';

/** The fixed accent palette (theme-chroma.js → PALETTE; contract AccentToken). */
export const PALETTE: Readonly<Record<AccentToken, string>> = {
  orange: '#ec8b3a',
  teal: '#45b1ae',
  violet: '#a489d9',
  green: '#6fbf6a',
  blue: '#5a93dd',
  rose: '#d97a8d',
  amber: '#e8b441',
  slate: '#7e8696',
};

/** The themes the Visu app derives tokens for. */
export type Theme = 'light' | 'dark' | 'image';

export const THEMES: readonly Theme[] = ['light', 'dark', 'image'];

/** Representative surface luminance per theme — the contrast target. */
export const SURFACE: Readonly<Record<Theme, string>> = {
  light: '#f3f5f8',
  dark: '#1b2027',
  image: '#3a3f49',
};

/** WCAG AA contrast ratio for normal text. */
export const AA = 4.5;

/** Spacing base step in px (4 px grid) — Tokens.space(step) = step * BASE. */
const SPACE_BASE = 4;

/** The Visu app font stack. */
const FONT =
  "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";

/** Text colour that sits legibly ON a filled swatch of `c`. */
export function ink(c: string): string {
  return chroma.contrast(c, '#ffffff') >= chroma.contrast(c, '#171307')
    ? '#ffffff'
    : '#171307';
}

/**
 * Nudge `c` (lighter on dark surfaces, darker on light) until it clears AA vs
 * `surface`. Bounded loop — on the extreme case (no separation reachable by
 * nudging in the chosen direction) it falls back to the AA-clearing ink so the
 * result is always legible.
 */
export function readableOn(c: string, surface: string): string {
  let col = chroma(c);
  const lift = chroma(surface).luminance() < 0.4;
  let guard = 0;
  while (chroma.contrast(col, surface) < AA && guard++ < 40) {
    col = lift ? col.brighten(0.1) : col.darken(0.1);
  }
  if (chroma.contrast(col, surface) < AA) {
    // Extreme fallback: nudging saturated colour ran out of range — use the ink
    // colour that is guaranteed to clear AA against this surface.
    return ink(surface);
  }
  return col.hex();
}

/**
 * Resolve an accent given as a palette key OR a raw hex (theme-chroma.js parity).
 * Unknown tokens that are not a chroma-parseable colour fall back to the default
 * accent instead of crashing the renderer (contract: `accent(token)` is total).
 */
function resolveAccent(accent: string): string {
  const byKey = (PALETTE as Record<string, string>)[accent];
  if (byKey) return byKey;
  return chroma.valid(accent) ? accent : PALETTE.orange;
}

/**
 * Compute the full set of CSS custom properties for `theme` with the active
 * `accent` (palette key or hex). Mirrors theme-chroma.js `apply()`, but returns
 * a plain map instead of writing to `document` — the host decides where to
 * apply it, keeping this module pure and testable.
 */
export function cssVars(theme: Theme, accent: AccentToken | string): Record<string, string> {
  const surface = SURFACE[theme] ?? SURFACE.light;
  const vars: Record<string, string> = {};

  for (const key of Object.keys(PALETTE) as AccentToken[]) {
    const hex = PALETTE[key];
    vars[`--vz-acc-${key}-ink`] = ink(hex);
    vars[`--vz-acc-${key}-readable`] = readableOn(hex, surface);
  }

  const a = resolveAccent(accent);
  vars['--vz-accent-ink'] = ink(a);
  vars['--vz-accent-readable'] = readableOn(a, surface);
  vars['--vz-accent-shade'] = chroma(a).darken(0.65).hex();
  vars['--vz-accent-tint'] = chroma(a).brighten(0.65).hex();

  // clock / date pill — a muted-but-AA ink derived from a neutral gray, so the
  // date stays legible on every theme (incl. glass over a bright photo).
  vars['--vz-clock-ink'] = readableOn('#8b909b', surface);

  return vars;
}

/**
 * Build the `Tokens` surface (CONTRACT-v1.md §5) renderers receive for the
 * active `theme` + `accent`. AA-safe by construction: `accent()` returns the
 * readable-on-surface colour, `accentInk()` the legible foreground for a filled
 * swatch.
 */
export function makeTokens(theme: Theme, accent: AccentToken | string): Tokens {
  const surface = SURFACE[theme] ?? SURFACE.light;
  void accent; // active accent influences cssVars(), not the per-token lookups
  return {
    accent(token: string): string {
      return readableOn(resolveAccent(token), surface);
    },
    accentInk(token: string): string {
      return ink(resolveAccent(token));
    },
    font: FONT,
    space(step: number): string {
      return `${step * SPACE_BASE}px`;
    },
  };
}
