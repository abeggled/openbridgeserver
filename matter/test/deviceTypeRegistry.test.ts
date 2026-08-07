import { describe, expect, it } from 'vitest';

import { resolveMatterMappings } from '../src/deviceTypeRegistry.js';
import type { ObsDataPoint } from '../src/obsApiClient.js';

function dataPoint(overrides: Partial<ObsDataPoint>): ObsDataPoint {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'Test DataPoint',
    data_type: 'BOOLEAN',
    unit: null,
    tags: [],
    mqtt_topic: 'dp/11111111-1111-1111-1111-111111111111/value',
    ...overrides,
  };
}

describe('resolveMatterMappings', () => {
  it('maps a DataPoint tagged matter:onoff to the onoff device type', () => {
    const dp = dataPoint({ tags: ['matter:onoff'] });
    expect(resolveMatterMappings([dp])).toEqual([{ dataPoint: dp, deviceType: 'onoff' }]);
  });

  it('maps a DataPoint tagged matter:temperature to the temperature device type', () => {
    const dp = dataPoint({ tags: ['other-tag', 'matter:temperature'] });
    expect(resolveMatterMappings([dp])).toEqual([{ dataPoint: dp, deviceType: 'temperature' }]);
  });

  it('ignores DataPoints without a matter: tag', () => {
    const dp = dataPoint({ tags: ['some-tag'] });
    expect(resolveMatterMappings([dp])).toEqual([]);
  });

  it('ignores DataPoints with no tags at all', () => {
    const dp = dataPoint({ tags: [] });
    expect(resolveMatterMappings([dp])).toEqual([]);
  });

  it('ignores an unsupported matter: device type', () => {
    const dp = dataPoint({ tags: ['matter:thermostat'] });
    expect(resolveMatterMappings([dp])).toEqual([]);
  });

  it('uses the first supported matter: tag when multiple are present', () => {
    const dp = dataPoint({ tags: ['matter:thermostat', 'matter:onoff'] });
    expect(resolveMatterMappings([dp])).toEqual([{ dataPoint: dp, deviceType: 'onoff' }]);
  });

  it('maps multiple DataPoints independently', () => {
    const onoff = dataPoint({ id: 'a', tags: ['matter:onoff'] });
    const temperature = dataPoint({ id: 'b', tags: ['matter:temperature'] });
    const untagged = dataPoint({ id: 'c', tags: [] });
    expect(resolveMatterMappings([onoff, temperature, untagged])).toEqual([
      { dataPoint: onoff, deviceType: 'onoff' },
      { dataPoint: temperature, deviceType: 'temperature' },
    ]);
  });
});
