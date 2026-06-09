import { describe, it, expect, beforeAll } from 'vitest';
import Ajv from 'ajv';
import type { ValidateFunction } from 'ajv';
import addFormats from 'ajv-formats';
import schema from '../contract.schema.json' with { type: 'json' };
import fixtures from '../fixtures.json' with { type: 'json' };

// Kern-Typen (stabiler Kern v1) und ihre erwarteten Fixture-Zustände — CONTRACT-v1.md §3/§4.
const CORE_TYPES = ['light', 'switch', 'blind', 'jalousie', 'sensor', 'scene'] as const;
const RESERVED_TYPES = ['climate', 'weather', 'energy', 'chart', 'media', 'camera', 'alarm'] as const;
const EXPECTED_STATES: Record<(typeof CORE_TYPES)[number], string[]> = {
  light: ['off', 'on', 'dimmed'],
  switch: ['off', 'on'],
  blind: ['open', 'half', 'locked'],
  jalousie: ['open', 'tilted', 'locked'],
  sensor: ['ok', 'warn'],
  scene: ['film', 'morgen'],
};

const ROLES = ['compact', 'default', 'wide', 'tall', 'feature', 'banner'];
const ICON_SLOTS = [
  'bulb', 'blind', 'thermo', 'wind', 'sun', 'cloud', 'cam', 'shield',
  'bolt', 'scene', 'sparkle', 'lock', 'play', 'pause', 'skip',
];

const s = schema as Record<string, any>;
const f = fixtures as Record<string, any>;

describe('contract.schema.json — Globals (§2)', () => {
  it('declares version 1.0', () => {
    expect(s.version).toBe('1.0');
  });

  it('declares roles exactly [compact,default,wide,tall,feature,banner]', () => {
    expect(s.roles).toEqual(ROLES);
  });

  it('declares iconSlots exactly as in §2', () => {
    expect(s.iconSlots).toEqual(ICON_SLOTS);
  });

  it('carries a widgets block', () => {
    expect(s.widgets).toBeTypeOf('object');
  });
});

describe('contract.schema.json — widget types (§3)', () => {
  it('declares all 6 core types', () => {
    for (const t of CORE_TYPES) {
      expect(s.widgets, `core type ${t}`).toHaveProperty(t);
    }
  });

  it('declares all 7 reserved v1.1 types', () => {
    for (const t of RESERVED_TYPES) {
      expect(s.widgets, `reserved type ${t}`).toHaveProperty(t);
    }
  });

  it('marks reserved types as reserved (not part of stable core)', () => {
    for (const t of RESERVED_TYPES) {
      expect(s.widgets[t].reserved, `${t}.reserved`).toBe(true);
    }
  });

  it('does not mark core types as reserved', () => {
    for (const t of CORE_TYPES) {
      expect(s.widgets[t].reserved, `${t}.reserved`).not.toBe(true);
    }
  });

  it('each core type carries data/actions/icon/roles', () => {
    for (const t of CORE_TYPES) {
      const w = s.widgets[t];
      expect(w, `${t}.data`).toHaveProperty('data');
      expect(w, `${t}.actions`).toHaveProperty('actions');
      expect(w, `${t}.icon`).toHaveProperty('icon');
      expect(w, `${t}.roles`).toHaveProperty('roles');
      expect(ROLES, `${t}.roles.default valid`).toContain(w.roles.default);
      for (const r of w.roles.allow) expect(ROLES, `${t}.roles.allow ${r}`).toContain(r);
    }
  });

  it('light: data fields, actions, icon, roles per §3', () => {
    const w = s.widgets.light;
    expect(Object.keys(w.data).sort()).toEqual(['accent', 'dim', 'label', 'on', 'room'].sort());
    expect(Object.keys(w.actions).sort()).toEqual(['setDim', 'toggle'].sort());
    expect(w.icon).toBe('bulb');
    expect(w.roles).toEqual({ allow: ['compact', 'default', 'wide'], default: 'default' });
  });

  it('switch: per §3', () => {
    const w = s.widgets.switch;
    expect(Object.keys(w.data).sort()).toEqual(['accent', 'label', 'on', 'room'].sort());
    expect(Object.keys(w.actions)).toEqual(['toggle']);
    expect(w.icon).toBe('wind');
    expect(w.roles).toEqual({ allow: ['compact', 'default'], default: 'compact' });
  });

  it('blind: position 0=auf/100=zu, locked, actions per §3', () => {
    const w = s.widgets.blind;
    expect(Object.keys(w.data).sort()).toEqual(['accent', 'label', 'locked', 'position', 'room'].sort());
    expect(Object.keys(w.actions).sort()).toEqual(['lock', 'setPosition', 'unlock'].sort());
    expect(w.icon).toBe('blind');
    expect(w.roles).toEqual({ allow: ['compact', 'default', 'wide', 'tall'], default: 'default' });
  });

  it('jalousie: position/slat/locked/invert/moving/statuses/mode per §3', () => {
    const w = s.widgets.jalousie;
    expect(Object.keys(w.data).sort()).toEqual(
      ['accent', 'invert', 'label', 'locked', 'mode', 'moving', 'position', 'room', 'slat', 'statuses'].sort(),
    );
    expect(Object.keys(w.actions).sort()).toEqual(['lock', 'setPosition', 'setSlat', 'unlock'].sort());
    expect(w.icon).toBe('blind');
    expect(w.roles).toEqual({ allow: ['default', 'wide', 'tall', 'feature'], default: 'wide' });
  });

  it('sensor: read-only (no actions) per §3', () => {
    const w = s.widgets.sensor;
    expect(Object.keys(w.data).sort()).toEqual(['accent', 'label', 'room', 'status', 'unit', 'value'].sort());
    expect(Object.keys(w.actions)).toEqual([]);
    expect(w.icon).toBe('thermo');
    expect(w.roles).toEqual({ allow: ['compact', 'default'], default: 'compact' });
  });

  it('scene: icon/sub, activateScene per §3', () => {
    const w = s.widgets.scene;
    expect(Object.keys(w.data).sort()).toEqual(['accent', 'icon', 'label', 'room', 'sub'].sort());
    expect(Object.keys(w.actions)).toEqual(['activateScene']);
    expect(w.icon).toBe('scene');
    expect(w.roles).toEqual({ allow: ['compact', 'default', 'wide'], default: 'default' });
  });
});

describe('fixtures.json — completeness (§4)', () => {
  it('declares contractVersion 1.0', () => {
    expect(f.contractVersion).toBe('1.0');
  });

  it('provides every core type with its expected states', () => {
    for (const t of CORE_TYPES) {
      expect(f, `fixtures.${t}`).toHaveProperty(t);
      expect(Object.keys(f[t]).sort(), `${t} states`).toEqual([...EXPECTED_STATES[t]].sort());
    }
  });

  it('uses accent palette keys, never hex', () => {
    for (const t of CORE_TYPES) {
      for (const state of Object.keys(f[t])) {
        const accent = f[t][state].accent;
        if (accent !== undefined) {
          expect(String(accent), `${t}.${state}.accent`).not.toMatch(/^#/);
        }
      }
    }
  });
});

describe('ajv — every fixture validates against the schema', () => {
  let ajv: Ajv;
  const validators: Record<string, ValidateFunction> = {};

  beforeAll(() => {
    ajv = new Ajv({ allErrors: true, strict: false, validateSchema: false });
    addFormats(ajv);
    // Register the whole contract so absolute $id references (accent palette) resolve.
    // ($schema declares the contract namespace, not a JSON-Schema meta-schema.)
    ajv.addSchema(s, 'contract');
    // Compile a per-type validator from the widget's data subschema.
    for (const t of CORE_TYPES) {
      const dataSchema = s.widgets[t].dataSchema;
      expect(dataSchema, `widgets.${t}.dataSchema present`).toBeTypeOf('object');
      validators[t] = ajv.compile(dataSchema);
    }
  });

  it('schema as a whole is a valid JSON Schema (registered without throwing)', () => {
    expect(ajv.getSchema('contract')).toBeTypeOf('function');
  });

  for (const t of CORE_TYPES) {
    it(`every ${t} fixture validates`, () => {
      for (const state of Object.keys(f[t])) {
        const ok = validators[t](f[t][state]);
        expect(ok, `${t}.${state}: ${ajv.errorsText(validators[t].errors)}`).toBe(true);
      }
    });
  }
});
