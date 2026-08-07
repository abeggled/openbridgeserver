import { bridgedNode, MatterbridgeEndpoint, onOffPlugInUnit, powerSource, type MatterbridgePlatform } from 'matterbridge';
import type { AnsiLogger } from 'matterbridge/logger';
import { OnOff } from 'matterbridge/matter/clusters';

import type { MatterMapping } from '../deviceTypeRegistry.js';
import type { ObsMqttClient } from '../mqttClient.js';

// Matter's reserved test-vendor range — this plugin is uncertified for now (see discussion #357).
const VENDOR_ID = 0xfff1;
const VENDOR_NAME = 'OpenBridgeServer';

/**
 * Creates a read/write Matter OnOffSwitch (generic outlet/actuator, not lighting-specific) for
 * a DataPoint tagged `matter:onoff` — the first write path: an Alexa/HomeKit "on"/"off" command
 * publishes to dp/{uuid}/set, and OBS's WriteRouter forwards it to the bound adapter.
 */
export async function createOnOffSwitch(platform: MatterbridgePlatform, mqtt: ObsMqttClient, mapping: MatterMapping, log: AnsiLogger): Promise<void> {
  const { dataPoint } = mapping;

  const endpoint = new MatterbridgeEndpoint([onOffPlugInUnit, bridgedNode, powerSource], { id: dataPoint.id }, false)
    .createDefaultIdentifyClusterServer()
    .createDefaultBridgedDeviceBasicInformationClusterServer(dataPoint.name, dataPoint.id, VENDOR_ID, VENDOR_NAME, dataPoint.name)
    .createDefaultOnOffClusterServer()
    .createDefaultPowerSourceWiredClusterServer()
    .addRequiredClusterServers();

  await platform.registerDevice(endpoint);

  endpoint.addCommandHandler('on', () => {
    mqtt.publishSet(dataPoint.id, 'true');
  });
  endpoint.addCommandHandler('off', () => {
    mqtt.publishSet(dataPoint.id, 'false');
  });

  mqtt.subscribeToValue(dataPoint.id, (_id, payload) => {
    const on = coerceBoolean(payload.v);
    if (on === undefined) return;
    void endpoint.updateAttribute(OnOff.Cluster, 'onOff', on, log);
  });
}

function coerceBoolean(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
    if (['false', '0', 'no', 'off'].includes(normalized)) return false;
  }
  return undefined;
}
