import { bridgedNode, MatterbridgeEndpoint, powerSource, temperatureSensor, type MatterbridgePlatform } from 'matterbridge';
import type { AnsiLogger } from 'matterbridge/logger';
import { TemperatureMeasurement } from 'matterbridge/matter/clusters';

import type { MatterMapping } from '../deviceTypeRegistry.js';
import type { ObsMqttClient } from '../mqttClient.js';

// Matter's reserved test-vendor range — this plugin is uncertified for now (see discussion #357).
const VENDOR_ID = 0xfff1;
const VENDOR_NAME = 'OpenBridgeServer';

/**
 * Creates a read-only Matter TemperatureSensor for a DataPoint tagged `matter:temperature`.
 * MeasuredValue is centidegrees Celsius per the Matter spec (10.00°C -> 1000).
 */
export async function createTemperatureSensor(platform: MatterbridgePlatform, mqtt: ObsMqttClient, mapping: MatterMapping, log: AnsiLogger): Promise<void> {
  const { dataPoint } = mapping;

  const endpoint = new MatterbridgeEndpoint([temperatureSensor, bridgedNode, powerSource], { id: dataPoint.id }, false)
    .createDefaultIdentifyClusterServer()
    .createDefaultBridgedDeviceBasicInformationClusterServer(dataPoint.name, dataPoint.id, VENDOR_ID, VENDOR_NAME, dataPoint.name)
    .createDefaultTemperatureMeasurementClusterServer(null)
    .createDefaultPowerSourceWiredClusterServer()
    .addRequiredClusterServers();

  await platform.registerDevice(endpoint);

  mqtt.subscribeToValue(dataPoint.id, (_id, payload) => {
    const celsius = typeof payload.v === 'number' ? payload.v : Number(payload.v);
    if (Number.isNaN(celsius)) return;
    void endpoint.updateAttribute(TemperatureMeasurement.Cluster, 'measuredValue', Math.round(celsius * 100), log);
  });
}
