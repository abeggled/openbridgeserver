/**
 * skin-host/dispatch — type-addressed renderer selection (A1, Issue #97).
 *
 * Goldene Regel 2: a renderer is addressed by the device TYPE (`tiles[type]`),
 * never selected by a `switch` with a silent default. Goldene Regel 3: "not
 * supported" is a declared fact in `manifest.unsupported`, not a forgotten case.
 *
 * {@link selectTile} encodes exactly that contract as a pure function so it is
 * unit-testable without mounting Vue:
 *
 *   - type present in `tiles`            → return that renderer.
 *   - type declared in `unsupported`     → return `null` (a deliberate skip the
 *                                          host renders as a quiet placeholder).
 *   - type missing AND not unsupported   → throw a visible `gap` error.
 *
 * The thrown gap is the "never a silent lamp" rule: a page that asks for a type
 * the skin neither renders nor declared unsupported is a hard, surfaced failure.
 */

import type { Renderer, SkinManifest, CoreWidgetType } from '@obs/visu-contract';
import type { RendererMap } from './skins';

/** A renderer + the type it was selected for (or a declared-unsupported marker). */
export interface TileSelection {
  /** The renderer for this type, or `null` when the type is declared unsupported. */
  readonly renderer: Renderer | null;
  /** True when the type is in `manifest.unsupported` (a deliberate, declared skip). */
  readonly unsupported: boolean;
}

/**
 * Select the tile renderer for a device `type` against a skin.
 *
 * @throws Error  if the type is neither in `tiles` nor declared `unsupported`
 *                — a `gap` we surface rather than rendering a silent default.
 */
export function selectTile(
  tiles: RendererMap,
  manifest: SkinManifest,
  type: CoreWidgetType | string,
): TileSelection {
  const renderer = (tiles as Record<string, Renderer | undefined>)[type];
  if (renderer) return { renderer, unsupported: false };

  if (manifest.unsupported.includes(type)) {
    return { renderer: null, unsupported: true };
  }

  throw new Error(
    `skin-host: no tile renderer for type "${type}" in skin "${manifest.name}", ` +
      `and "${type}" is not declared in manifest.unsupported — this is a gap, not a silent default. ` +
      `Either the skin must ship a renderer for "${type}" or declare it unsupported.`,
  );
}
