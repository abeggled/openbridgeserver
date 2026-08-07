import type { ObsDataPoint } from './obsApiClient.js';

/**
 * Matter device types this plugin can expose. Kept intentionally small for the first slice —
 * see matter/README.md for the full M2/M3 roadmap of additional device types.
 */
export type MatterDeviceType = 'temperature' | 'onoff';

export interface MatterMapping {
  dataPoint: ObsDataPoint;
  deviceType: MatterDeviceType;
}

const TAG_PREFIX = 'matter:';
const SUPPORTED_TYPES = new Set<MatterDeviceType>(['temperature', 'onoff']);

/**
 * Opt-in mapping mechanism: a DataPoint is exposed to Matter only if it carries a tag of the
 * form `matter:<devicetype>` (e.g. `matter:onoff`). Reuses the existing DataPoint `tags` field
 * instead of the not-yet-built `matter_config` table (see issue #56 discussion) — avoids
 * accidentally exposing every DataPoint to voice assistants.
 */
export function resolveMatterMappings(dataPoints: ObsDataPoint[]): MatterMapping[] {
  const mappings: MatterMapping[] = [];
  for (const dataPoint of dataPoints) {
    const deviceType = findMatterTag(dataPoint.tags);
    if (deviceType) {
      mappings.push({ dataPoint, deviceType });
    }
  }
  return mappings;
}

function findMatterTag(tags: string[]): MatterDeviceType | undefined {
  for (const tag of tags) {
    if (!tag.startsWith(TAG_PREFIX)) continue;
    const candidate = tag.slice(TAG_PREFIX.length);
    if (isSupportedType(candidate)) return candidate;
  }
  return undefined;
}

function isSupportedType(value: string): value is MatterDeviceType {
  return SUPPORTED_TYPES.has(value as MatterDeviceType);
}
