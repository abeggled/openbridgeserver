import type { PlatformConfig } from 'matterbridge';

/**
 * Custom config fields for the matterbridge-obs plugin. Matterbridge persists this in
 * `.matterbridge/matterbridge-obs.config.json` and renders an editable form for it in its
 * frontend, driven by `matterbridge-obs.schema.json` (see README.md).
 */
export interface ObsPlatformConfig extends PlatformConfig {
  obsApiUrl?: string;
  obsApiKey?: string;
  mqttHost?: string;
  mqttPort?: number;
  mqttUsername?: string;
  mqttPassword?: string;
}

export interface ResolvedObsConfig {
  obsApiUrl: string;
  obsApiKey: string;
  mqttHost: string;
  mqttPort: number;
  mqttUsername?: string;
  mqttPassword?: string;
}

const DEFAULT_MQTT_HOST = 'localhost';
const DEFAULT_MQTT_PORT = 1883;

/**
 * Validates and normalizes the raw plugin config. Throws with a message the plugin author
 * (or the Matterbridge frontend log) surfaces directly, since obsApiUrl/obsApiKey have no
 * sensible default — the plugin cannot start without them.
 */
export function resolveConfig(config: ObsPlatformConfig): ResolvedObsConfig {
  if (!config.obsApiUrl) {
    throw new Error('matterbridge-obs: config field "obsApiUrl" is required (e.g. http://localhost:8080)');
  }
  if (!config.obsApiKey) {
    throw new Error('matterbridge-obs: config field "obsApiKey" is required — create an API key in the OBS Admin GUI (Settings → API Keys)');
  }

  return {
    obsApiUrl: config.obsApiUrl.replace(/\/+$/, ''),
    obsApiKey: config.obsApiKey,
    mqttHost: config.mqttHost ?? DEFAULT_MQTT_HOST,
    mqttPort: config.mqttPort ?? DEFAULT_MQTT_PORT,
    mqttUsername: config.mqttUsername,
    mqttPassword: config.mqttPassword,
  };
}
