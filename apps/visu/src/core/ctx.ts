/**
 * core/ctx — the shared helpers a renderer receives (CONTRACT-v1.md §5).
 *
 * This is the sandbox boundary (Goldene Regel 4): a renderer gets ONLY these
 * helpers, never core internals or the host state. The helpers own no state
 * and execute no device action (Goldene Regel 1) — they are pure read-only
 * derivations over a {@link Device}.
 *
 * Behaviour is ported 1:1 from the prototype:
 *  - `nf` / `softHyphenate`  ← reference/vue-ionic/store.js
 *  - footer `stateText`      ← reference/vue-ionic/widgets.js (vz-tile-foot block)
 *  - `DEFAULT_ICONS`         ← reference/vue-ionic/store.js → ICONS
 */
import type { Ctx, Device } from '@obs/visu-contract';

/** Soft hyphen (U+00AD) — invisible until the browser needs to break the line. */
const SHY = '­';
/** Non-breaking space (U+00A0) — keeps a value glued to its unit. */
const NBSP = ' ';

/* ----------------------------------------------------------- nf (de-DE) --- */

/**
 * German number formatting: decimal comma, thousands point.
 * Ported from store.js `nf`. A comma-decimal string is parsed first; null /
 * NaN render as an en dash. `dec` defaults to 0 for integers, else 1.
 */
function nf(v: number | string, dec?: number): string {
  const n: number = typeof v === 'string' ? parseFloat(v.replace(',', '.')) : v;
  if (n == null || Number.isNaN(n)) return '–';
  const d = dec != null ? dec : Number.isInteger(n) ? 0 : 1;
  return n.toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/* --------------------------------------------------- softHyphenate (de) --- */

/**
 * Second elements of common Haustechnik compounds — break right before them.
 * Verbatim from store.js so long labels (e.g. "Pendelleuchte") break tastefully.
 */
const SEGMENTS = [
  'leuchte', 'leuchten', 'lampe', 'licht', 'lichter', 'anlage', 'melder',
  'sensor', 'lüfter', 'spiegel', 'schalter', 'steckdose', 'heizung',
  'verlauf', 'fenster', 'rollladen', 'vorhang', 'abend', 'morgen',
  'decke', 'decken', 'wand', 'boden',
];

/**
 * Insert soft hyphens at compound-word boundaries (store.js `softHyphenate`).
 * Short words and whitespace are left untouched; never double-inserts a SHY.
 */
function hyphenate(text: string): string {
  if (!text || typeof text !== 'string') return text;
  return text
    .split(/(\s+)/)
    .map((word) => {
      if (word.length < 9 || /\s/.test(word)) return word;
      let out = word;
      for (const seg of SEGMENTS) {
        const re = new RegExp('(.{3,})(' + seg + ')', 'i');
        out = out.replace(re, (m, a: string, b: string) => (a.endsWith(SHY) ? m : a + SHY + b));
      }
      return out;
    })
    .join('');
}

/* ------------------------------------------------------------ stateText --- */

/** Blind/jalousie position label (widgets.js): 0 → Offen, 100 → Zu, sonst Teil. */
function positionWord(position: number): string {
  if (position === 0) return 'Offen';
  if (position === 100) return 'Zu';
  return 'Teil';
}

/**
 * Centralised footer text so every skin phrases it identically (§5):
 * "Aus" · "Ein" · "Ein — 45 %" · "62 % · Teil". Mirrors the vz-tile-foot
 * branch of widgets.js for the stable core types.
 */
function stateText(d: Device): string {
  switch (d.type) {
    case 'light':
      return d.dim != null ? `Ein — ${d.dim}${NBSP}%` : d.on ? 'Ein' : 'Aus';
    case 'switch':
      return d.on ? 'An' : 'Aus';
    case 'blind':
    case 'jalousie':
      return `${d.position}${NBSP}% · ${positionWord(d.position)}`;
    case 'sensor':
      return d.status ?? '';
    case 'scene':
      return d.sub ?? '';
    default:
      return '';
  }
}

/* ----------------------------------------------------------------- warn --- */

/** Is a sensor outside its comfort range? Any status other than "komfort". */
function warn(d: Device): boolean {
  return d.type === 'sensor' && d.status != null && d.status !== 'komfort';
}

/* ----------------------------------------------------------------- icon --- */

/**
 * Default icon set — inline SVG bodies, verbatim from store.js → ICONS.
 * A skin may ship its own set; a missing slot falls back to this (§2/§7).
 */
export const DEFAULT_ICONS: Readonly<Record<string, string>> = {
  bulb: `<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.8.6 1.5 1.5 1.5 2.5v.6h4v-.6c0-1 .7-1.9 1.5-2.5A6 6 0 0 0 12 3z"/>`,
  'chev-down': `<polyline points="6 9 12 15 18 9"/>`,
  'chev-up': `<polyline points="6 15 12 9 18 15"/>`,
  'chev-dd': `<polyline points="6 7 12 13 18 7"/><polyline points="6 13 12 19 18 13"/>`,
  'chev-uu': `<polyline points="6 17 12 11 18 17"/><polyline points="6 11 12 5 18 11"/>`,
  chevR: `<polyline points="9 6 15 12 9 18"/>`,
  stop: `<rect x="6" y="6" width="12" height="12" rx="2"/>`,
  lock: `<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>`,
  x: `<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>`,
  menu: `<line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/>`,
  msg: `<path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 4V6a1 1 0 0 1 1-1z"/>`,
  thermo: `<path d="M14 14.76V4a2 2 0 1 0-4 0v10.76a4 4 0 1 0 4 0z"/>`,
  wind: `<path d="M9.6 4.6A2 2 0 1 1 11 8H2"/><path d="M12.6 19.4A2 2 0 1 0 14 16H2"/><path d="M17.6 7.6A2.5 2.5 0 1 1 19 12H2"/>`,
  cloud: `<path d="M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1.6A4 4 0 1 1 17 18z"/>`,
  cam: `<rect x="3" y="6" width="14" height="12" rx="2"/><polygon points="21 8 17 12 21 16"/>`,
  shield: `<path d="M12 3l8 3v6c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V6z"/>`,
  bolt: `<polygon points="13 2 4 14 11 14 10 22 20 9 13 9 13 2"/>`,
  play: `<polygon points="7 5 19 12 7 19"/>`,
  pause: `<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>`,
  skip: `<polygon points="6 5 14 12 6 19"/><rect x="15" y="5" width="3" height="14"/>`,
  scene: `<path d="M12 3l2.5 5.5L20 10l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-1.5L12 3z"/>`,
  sparkle: `<path d="M12 2l1.8 5.7L19.5 9l-5.7 1.8L12 16l-1.8-5.2L4.5 9l5.7-1.3z"/>`,
  plus: `<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>`,
  minus: `<line x1="5" y1="12" x2="19" y2="12"/>`,
  search: `<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/>`,
};

/**
 * Resolve an icon body for a slot: skin set first, then the default set, then
 * empty (§7). The device is accepted for API symmetry with the contract `Ctx`
 * signature; today the slot alone selects the glyph.
 */
function icon(_d: Device, slot: string): string {
  return DEFAULT_ICONS[slot] ?? '';
}

/** The frozen helper bundle handed to renderers — the entire sandbox surface. */
export const ctx: Ctx = Object.freeze({
  stateText,
  hyphenate,
  icon,
  nf,
  warn,
});
