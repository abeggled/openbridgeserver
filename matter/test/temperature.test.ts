import { describe, expect, it } from 'vitest';

import { toMeasuredTemperature } from '../src/temperature.js';

describe('toMeasuredTemperature', () => {
  it('converts a number to centidegrees', () => {
    expect(toMeasuredTemperature(21.5)).toBe(2150);
    expect(toMeasuredTemperature(-3.456)).toBe(-346);
    expect(toMeasuredTemperature(0)).toBe(0);
  });

  it('converts a numeric string to centidegrees', () => {
    expect(toMeasuredTemperature(' 10 ')).toBe(1000);
  });

  it('passes null through as unknown instead of 0°C', () => {
    expect(toMeasuredTemperature(null)).toBeNull();
  });

  it('ignores non-finite numbers', () => {
    expect(toMeasuredTemperature(Number.NaN)).toBeUndefined();
    expect(toMeasuredTemperature(Number.POSITIVE_INFINITY)).toBeUndefined();
  });

  it('ignores empty and non-numeric strings', () => {
    expect(toMeasuredTemperature('')).toBeUndefined();
    expect(toMeasuredTemperature('   ')).toBeUndefined();
    expect(toMeasuredTemperature('warm')).toBeUndefined();
  });

  it('ignores values that are not temperatures', () => {
    expect(toMeasuredTemperature(true)).toBeUndefined();
    expect(toMeasuredTemperature(undefined)).toBeUndefined();
    expect(toMeasuredTemperature({ v: 1 })).toBeUndefined();
  });
});
