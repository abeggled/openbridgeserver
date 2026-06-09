// @obs/visu-contract — shared types (CONTRACT-v1.md §5/§7/§8).
// Golden rule 7: data + types only — this module declares no runtime behaviour.
// Golden rules 1/4: a Device is read-only for skins; renderers are pure functions
// over read-only data, the host owns state and maps gestures to canonical actions.

/* ------------------------------------------------------------------ Globals */

/** Layout roles (prominence, not pixels) — CONTRACT-v1.md §2. */
export type Role = 'compact' | 'default' | 'wide' | 'tall' | 'feature' | 'banner';

/** Semantic icon slots — CONTRACT-v1.md §2 (default set: store.js → ICONS). */
export type IconSlot =
  | 'bulb' | 'blind' | 'thermo' | 'wind' | 'sun' | 'cloud' | 'cam' | 'shield'
  | 'bolt' | 'scene' | 'sparkle' | 'lock' | 'play' | 'pause' | 'skip';

/** Accent palette keys — never raw hex in the contract (store.js → ACCENTS). */
export type AccentToken =
  | 'orange' | 'teal' | 'violet' | 'green' | 'blue' | 'rose' | 'amber' | 'slate';

/** The stable core widget types of contract v1. */
export type CoreWidgetType =
  | 'light' | 'switch' | 'blind' | 'jalousie' | 'sensor' | 'scene';

/** Reserved-for-v1.1 widget types — declared so skins can opt out deliberately. */
export type ReservedWidgetType =
  | 'climate' | 'weather' | 'energy' | 'chart' | 'media' | 'camera' | 'alarm';

export type WidgetType = CoreWidgetType | ReservedWidgetType;

/* --------------------------------------------------------- Device unions §3 */

/** Fields shared by every device. Read-only — skins never mutate state. */
interface DeviceBase {
  readonly type: CoreWidgetType;
  readonly id?: string;
  readonly room: string;
  readonly label: string;
  readonly accent: AccentToken;
}

/** `light` — on/off plus optional brightness (`dim`, null = nicht dimmbar). */
export interface LightDevice extends DeviceBase {
  readonly type: 'light';
  readonly on: boolean;
  readonly dim: number | null;
}

/** `switch` — a plain on/off toggle. */
export interface SwitchDevice extends DeviceBase {
  readonly type: 'switch';
  readonly on: boolean;
}

/** `blind` (Rollladen) — position 0 = auf, 100 = zu. */
export interface BlindDevice extends DeviceBase {
  readonly type: 'blind';
  readonly position: number;
  readonly locked: boolean;
}

/** One entry of a jalousie status traffic light: true | false | null. */
export interface JalousieStatus {
  readonly label: string;
  readonly val: boolean | null;
}

/** `jalousie` — position (0 = auf, 100 = zu) plus slat angle (0–100 ⇒ 0–90°). */
export interface JalousieDevice extends DeviceBase {
  readonly type: 'jalousie';
  readonly mode: 'jalousie';
  readonly position: number;
  readonly slat: number;
  readonly locked: boolean;
  readonly invert?: boolean;
  readonly moving?: 'up' | 'down' | null;
  readonly statuses: readonly JalousieStatus[];
}

/** `sensor` — read-only reading (Aussage, kein Vergessen). */
export interface SensorDevice extends DeviceBase {
  readonly type: 'sensor';
  readonly value: number | string;
  readonly unit: string;
  readonly status?: string;
}

/** `scene` — activatable scene with its own icon slot + optional subtitle. */
export interface SceneDevice extends DeviceBase {
  readonly type: 'scene';
  readonly icon: string;
  readonly sub?: string;
}

/** Discriminated union of every core device. Read-only for skins (golden rule 1/4). */
export type Device =
  | LightDevice
  | SwitchDevice
  | BlindDevice
  | JalousieDevice
  | SensorDevice
  | SceneDevice;

/* ----------------------------------------------------- Tokens / Ctx (§5) -- */

/** Theme tokens handed to renderers — AA-safe colours, font, spacing. */
export interface Tokens {
  /** Palette key → AA-safe CSS colour. */
  accent(token: string): string;
  /** Palette key → AA-safe ink (foreground) colour. */
  accentInk(token: string): string;
  /** Active font family. */
  font: string;
  /** Spacing step → CSS length. */
  space(step: number): string;
}

/**
 * The shared helpers a renderer receives — and the *only* surface it gets.
 * This is the sandbox boundary: no access to core internals (golden rule 4).
 */
export interface Ctx {
  /** "Aus" · "Ein" · "Ein — 45 %" · "62 % · Teil" — centralised footer text. */
  stateText(d: Device): string;
  /** softHyphenate(): insert weiche Trennstellen into long labels. */
  hyphenate(text: string): string;
  /** Resolve an icon for a device: skin set → default fallback. */
  icon(d: Device, slot: string): string;
  /** de-DE number formatting (decimal comma, thousands point). */
  nf(v: number | string, dec?: number): string;
  /** Is a sensor outside its comfort range? */
  warn(d: Device): boolean;
}

/**
 * A renderer is a pure function over read-only data + sandbox helpers.
 * Returns markup (string) or a framework node (e.g. a Vue VNode → unknown).
 */
export type Renderer = (d: Device, t: Tokens, ctx: Ctx) => string | unknown;

/* ------------------------------------------------- Skin manifest (§7) ----- */

/** Canonical action names per widget type — CONTRACT-v1.md §6. */
export type WidgetAction =
  | 'toggle'
  | 'setDim'
  | 'setPosition'
  | 'setSlat'
  | 'lock'
  | 'unlock'
  | 'activateScene'
  | 'arm'
  | 'disarm';

/** Which canonical actions a skin wires up for a given type → full/partial/display. */
export interface SkinWidgetEntry {
  readonly actions: readonly WidgetAction[];
}

export interface SkinLayout {
  readonly model: string;
  readonly grid?: Record<string, unknown>;
  readonly honors?: readonly string[];
  readonly roleMap?: Record<string, unknown>;
}

export interface SkinTweak {
  readonly type: 'select' | 'slider';
  readonly options?: readonly string[];
  readonly min?: number;
  readonly max?: number;
  readonly step?: number;
  readonly default: string | number;
}

/** skins/<name>/manifest.json — CONTRACT-v1.md §7. */
export interface SkinManifest {
  readonly name: string;
  readonly targetsContract: string;
  readonly font?: { readonly family: string; readonly src?: string };
  readonly renderers?: string;
  readonly icons?: string;
  /** "Nicht unterstützt" ist Pflichtangabe (golden rule 3), kein Vergessen. */
  readonly unsupported: readonly string[];
  readonly widgets: Readonly<Partial<Record<WidgetType, SkinWidgetEntry>>>;
  readonly layout: SkinLayout;
  readonly tweaks?: Readonly<Record<string, SkinTweak>>;
  readonly themes?: readonly string[];
}

/* ---------------------------------------------- Support report (§8) ------- */

/** Conformance level the generator computes (never self-asserted). */
export type SupportLevel = 'full' | 'partial' | 'display' | 'unsupported' | 'gap' | 'broken';

export interface SupportSummary {
  readonly full: number;
  readonly partial: number;
  readonly display: number;
  readonly unsupported: number;
  readonly gap: number;
  readonly broken: number;
}

export interface SupportWidgetEntry {
  readonly level: SupportLevel;
  readonly render?: string;
  readonly actions?: string;
  readonly fixtures?: readonly string[];
  readonly reason?: string;
}

/** support.json — computed by the generator, CONTRACT-v1.md §8. */
export interface SupportReport {
  readonly skin: string;
  readonly targetsContract: string;
  readonly contractLatest: string;
  readonly generatedAt: string;
  readonly summary: SupportSummary;
  readonly widgets: Readonly<Record<string, SupportWidgetEntry>>;
  readonly layout?: Record<string, unknown>;
  readonly a11y?: Record<string, unknown>;
}
