import { MatterbridgeDynamicPlatform, type PlatformMatterbridge } from 'matterbridge';
import type { AnsiLogger } from 'matterbridge/logger';

import { resolveConfig, type ObsPlatformConfig } from './config.js';
import { createOnOffSwitch } from './devices/onOffSwitch.js';
import { createTemperatureSensor } from './devices/temperatureSensor.js';
import { resolveMatterMappings } from './deviceTypeRegistry.js';
import { ObsMqttClient } from './mqttClient.js';
import { ObsApiClient } from './obsApiClient.js';

export default function initializePlugin(matterbridge: PlatformMatterbridge, log: AnsiLogger, config: ObsPlatformConfig): ObsMatterbridgePlatform {
  return new ObsMatterbridgePlatform(matterbridge, log, config);
}

export class ObsMatterbridgePlatform extends MatterbridgeDynamicPlatform {
  private mqtt?: ObsMqttClient;

  constructor(matterbridge: PlatformMatterbridge, log: AnsiLogger, override config: ObsPlatformConfig) {
    super(matterbridge, log, config);
    this.log.info('matterbridge-obs: initializing platform');
  }

  override async onStart(reason?: string): Promise<void> {
    this.log.info('matterbridge-obs: onStart called with reason:', reason ?? 'none');
    await this.ready;
    await this.clearSelect();

    const resolved = resolveConfig(this.config);
    const apiClient = new ObsApiClient(resolved.obsApiUrl, resolved.obsApiKey);

    const mqtt = new ObsMqttClient(resolved.mqttHost, resolved.mqttPort, resolved.mqttUsername, resolved.mqttPassword);
    await mqtt.connect();
    this.mqtt = mqtt;

    const dataPoints = await apiClient.fetchAllDataPoints();
    const mappings = resolveMatterMappings(dataPoints);
    this.log.info(`matterbridge-obs: found ${mappings.length} DataPoint(s) tagged for Matter exposure out of ${dataPoints.length} total`);

    for (const mapping of mappings) {
      if (mapping.deviceType === 'temperature') {
        await createTemperatureSensor(this, mqtt, mapping, this.log);
      } else if (mapping.deviceType === 'onoff') {
        await createOnOffSwitch(this, mqtt, mapping, this.log);
      }
    }
  }

  override async onConfigure(): Promise<void> {
    await super.onConfigure();
    this.log.info('matterbridge-obs: onConfigure called');
  }

  override async onShutdown(reason?: string): Promise<void> {
    await super.onShutdown(reason);
    this.log.info('matterbridge-obs: onShutdown called with reason:', reason ?? 'none');
    await this.mqtt?.disconnect();
    if (this.config.unregisterOnShutdown) await this.unregisterAllDevices(500);
  }
}
