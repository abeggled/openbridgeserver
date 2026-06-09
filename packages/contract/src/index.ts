// @obs/visu-contract — versioned data/type contract between the obs Visu app and skins.
// Golden rule (7): the contract is data and types only — it executes nothing.
import schema from '../contract.schema.json' with { type: 'json' };
import fixtures from '../fixtures.json' with { type: 'json' };

/** The maschinenlesbare contract spec (roles · iconSlots · widgets). */
export { schema };
/** Sample states per type — the Prüfgrundlage for generator + Fixture-Wand. */
export { fixtures };

/** The contract version (matches package.json major.minor; §2 declares "1.0"). */
export const version: string = schema.version;

// Type surface — Device unions, Tokens, Ctx, Renderer, SkinManifest, SupportReport.
export type * from './types.js';
