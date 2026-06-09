import { describe, it, expectTypeOf } from 'vitest';
import type {
  Device,
  LightDevice,
  SwitchDevice,
  BlindDevice,
  JalousieDevice,
  SensorDevice,
  SceneDevice,
  Tokens,
  Ctx,
  Renderer,
  SkinManifest,
  SupportReport,
} from '../src/types.js';

describe('Device unions (§5) — readonly', () => {
  it('Device is the union of all core device shapes', () => {
    expectTypeOf<LightDevice>().toMatchTypeOf<Device>();
    expectTypeOf<SwitchDevice>().toMatchTypeOf<Device>();
    expectTypeOf<BlindDevice>().toMatchTypeOf<Device>();
    expectTypeOf<JalousieDevice>().toMatchTypeOf<Device>();
    expectTypeOf<SensorDevice>().toMatchTypeOf<Device>();
    expectTypeOf<SceneDevice>().toMatchTypeOf<Device>();
  });

  it('light device carries its discriminant and fields', () => {
    expectTypeOf<LightDevice['type']>().toEqualTypeOf<'light'>();
    expectTypeOf<LightDevice['on']>().toEqualTypeOf<boolean>();
    expectTypeOf<LightDevice['dim']>().toEqualTypeOf<number | null>();
  });

  it('blind/jalousie position is a number', () => {
    expectTypeOf<BlindDevice['position']>().toEqualTypeOf<number>();
    expectTypeOf<JalousieDevice['slat']>().toEqualTypeOf<number>();
    expectTypeOf<JalousieDevice['moving']>().toEqualTypeOf<'up' | 'down' | null>();
  });

  it('device fields are readonly (golden rule 1/4: skins read-only)', () => {
    // @ts-expect-error device fields are readonly
    const mutate = (d: LightDevice) => { d.on = true; };
    void mutate;
  });
});

describe('Tokens (§5)', () => {
  it('exposes accent/accentInk/font/space', () => {
    expectTypeOf<Tokens['accent']>().toEqualTypeOf<(token: string) => string>();
    expectTypeOf<Tokens['accentInk']>().toEqualTypeOf<(token: string) => string>();
    expectTypeOf<Tokens['font']>().toEqualTypeOf<string>();
    expectTypeOf<Tokens['space']>().toEqualTypeOf<(step: number) => string>();
  });
});

describe('Ctx (§5) — sandbox helpers', () => {
  it('exposes stateText/hyphenate/icon/nf/warn', () => {
    expectTypeOf<Ctx['stateText']>().toEqualTypeOf<(d: Device) => string>();
    expectTypeOf<Ctx['hyphenate']>().toEqualTypeOf<(text: string) => string>();
    expectTypeOf<Ctx['icon']>().toEqualTypeOf<(d: Device, slot: string) => string>();
    expectTypeOf<Ctx['warn']>().toEqualTypeOf<(d: Device) => boolean>();
    expectTypeOf<Ctx['nf']>().parameter(0).toEqualTypeOf<number | string>();
    expectTypeOf<Ctx['nf']>().returns.toEqualTypeOf<string>();
  });
});

describe('Renderer (§5)', () => {
  it('is a pure function (d,t,ctx) => string | VNode', () => {
    expectTypeOf<Renderer>().parameters.toEqualTypeOf<[Device, Tokens, Ctx]>();
    expectTypeOf<Renderer>().returns.toMatchTypeOf<string | unknown>();
  });
});

describe('SkinManifest (§7)', () => {
  it('carries name/targetsContract/renderers/unsupported/widgets/layout', () => {
    expectTypeOf<SkinManifest['name']>().toEqualTypeOf<string>();
    expectTypeOf<SkinManifest['targetsContract']>().toEqualTypeOf<string>();
    expectTypeOf<SkinManifest['unsupported']>().toEqualTypeOf<readonly string[]>();
    expectTypeOf<SkinManifest>().toHaveProperty('widgets');
    expectTypeOf<SkinManifest>().toHaveProperty('layout');
  });
});

describe('SupportReport (§8)', () => {
  it('carries skin/targetsContract/summary/widgets', () => {
    expectTypeOf<SupportReport['skin']>().toEqualTypeOf<string>();
    expectTypeOf<SupportReport['targetsContract']>().toEqualTypeOf<string>();
    expectTypeOf<SupportReport>().toHaveProperty('summary');
    expectTypeOf<SupportReport>().toHaveProperty('widgets');
  });
});
