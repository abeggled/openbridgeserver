/**
 * skin-host/skins — the skin registry (A1, Issue #97).
 *
 * The host is the only layer allowed to import a skin (Goldene Regeln 1/4):
 * the core owns the model + state, the skin reads it, and this module is the
 * single seam where a concrete skin (`@obs-visu-skins/ionic`) is pulled in and
 * normalised to the uniform {@link Skin} shape the host renders against.
 *
 * Each skin exports its renderer maps (`tiles`/`details`) and a `manifest`
 * (CONTRACT-v1.md §7). The npm package surfaces the manifest as a JSON
 * sub-export (`@obs-visu-skins/ionic/manifest.json`) and the maps from its
 * root; we re-assemble them here so the rest of the host depends only on the
 * contract-typed {@link Skin} record, never on a package layout.
 *
 * A page carries a `skin` key (the author's decision — there is no runtime skin
 * switch). {@link resolveSkin} turns that key into the {@link Skin} or throws a
 * visible error: an unknown skin key is a hard failure, never a silent default
 * (the same "never a silent lamp" discipline the renderer dispatch follows).
 */

import type { SkinManifest, Renderer, CoreWidgetType } from '@obs/visu-contract';

import { tiles as ionicTiles, details as ionicDetails } from '@obs-visu-skins/ionic';
import ionicManifest from '@obs-visu-skins/ionic/manifest.json';

/** A partial renderer map over the core widget types (mirror of the skin export). */
export type RendererMap = Partial<Record<CoreWidgetType, Renderer>>;

/** The uniform shape the host renders against — renderer maps + the manifest. */
export interface Skin {
  /** Tile renderers per core type — addressed by `tiles[device.type]` (golden rule 2). */
  readonly tiles: RendererMap;
  /** Optional detail-surface renderers per core type. */
  readonly details: RendererMap;
  /** The skin manifest (layout · widgets · unsupported · tweaks · themes). */
  readonly manifest: SkinManifest;
}

/**
 * The skin registry. Author-time set of skins the app ships with; a page picks
 * one by key. Adding a skin means importing it here and adding one entry — the
 * host code stays skin-agnostic.
 */
export const skins = {
  ionic: {
    tiles: ionicTiles,
    details: ionicDetails,
    manifest: ionicManifest as SkinManifest,
  },
} as const satisfies Record<string, Skin>;

/** The valid skin keys (author's choice on a page). */
export type SkinKey = keyof typeof skins;

/**
 * Resolve a skin key to its {@link Skin}. An unknown key is a hard, visible
 * failure — there is no silent fallback skin (a page that names a skin the app
 * does not ship is an authoring bug we surface, not paper over).
 */
export function resolveSkin(key: string): Skin {
  const skin = (skins as Record<string, Skin>)[key];
  if (!skin) {
    const known = Object.keys(skins).join(', ');
    throw new Error(
      `skin-host: unknown skin "${key}" — no such skin in the registry (known: ${known}).`,
    );
  }
  return skin;
}
