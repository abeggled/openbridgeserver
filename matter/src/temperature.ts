/**
 * Converts an OBS DataPoint value to a Matter TemperatureMeasurement MeasuredValue
 * (centidegrees Celsius, 10.00°C -> 1000).
 *
 * - `null`: OBS publishes `"v": null` for an unknown or non-finite value (NaN/Infinity, #1153).
 *   MeasuredValue is nullable in Matter and null means "unknown", so it is passed through
 *   instead of being read as 0°C.
 * - `undefined`: the value is not a temperature (boolean, empty or non-numeric string, object);
 *   the caller keeps the last valid reading.
 */
export function toMeasuredTemperature(value: unknown): number | null | undefined {
  if (value === null) return null;
  let celsius: number;
  if (typeof value === 'number') {
    celsius = value;
  } else if (typeof value === 'string' && value.trim() !== '') {
    celsius = Number(value);
  } else {
    return undefined;
  }
  return Number.isFinite(celsius) ? Math.round(celsius * 100) : undefined;
}
