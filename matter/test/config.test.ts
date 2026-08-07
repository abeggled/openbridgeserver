import { describe, expect, it } from 'vitest';

import { resolveConfig, type ObsPlatformConfig } from '../src/config.js';

function baseConfig(overrides: Partial<ObsPlatformConfig> = {}): ObsPlatformConfig {
  return {
    name: 'matterbridge-obs',
    type: 'DynamicPlatform',
    version: '0.1.0',
    debug: false,
    unregisterOnShutdown: false,
    obsApiUrl: 'http://localhost:8080',
    obsApiKey: 'obs_test_key',
    ...overrides,
  };
}

describe('resolveConfig', () => {
  it('applies MQTT defaults when not configured', () => {
    const resolved = resolveConfig(baseConfig());
    expect(resolved.mqttHost).toBe('localhost');
    expect(resolved.mqttPort).toBe(1883);
  });

  it('passes through explicit MQTT settings', () => {
    const resolved = resolveConfig(baseConfig({ mqttHost: 'mosquitto', mqttPort: 18830, mqttUsername: 'obs', mqttPassword: 'secret' }));
    expect(resolved).toMatchObject({ mqttHost: 'mosquitto', mqttPort: 18830, mqttUsername: 'obs', mqttPassword: 'secret' });
  });

  it('strips a trailing slash from obsApiUrl', () => {
    const resolved = resolveConfig(baseConfig({ obsApiUrl: 'http://localhost:8080/' }));
    expect(resolved.obsApiUrl).toBe('http://localhost:8080');
  });

  it('throws when obsApiUrl is missing', () => {
    expect(() => resolveConfig(baseConfig({ obsApiUrl: undefined }))).toThrow(/obsApiUrl/);
  });

  it('throws when obsApiKey is missing', () => {
    expect(() => resolveConfig(baseConfig({ obsApiKey: undefined }))).toThrow(/obsApiKey/);
  });
});
