// The numeric rule the backend applies to Logic values, kept in one place so
// the block card and the configuration panel cannot drift apart — the same
// reason logicBooleans.js exists.
//
// Mirrors GraphExecutor._to_num: None falls back to the default, a real
// boolean is 1/0, and anything else goes through Python's float(), which
// raises on a collection and on a non-numeric string and therefore also falls
// back to the default.

// The decimal/scientific syntax Python's float() accepts, minus the special
// inf/nan spellings that make no sense as a configured value.
export const BACKEND_NUMBER_RE = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/

export function toBackendNumberText(value, fallback = '0') {
  if (value === null || value === undefined) return fallback
  // A JSON import may carry a native boolean; float() never sees it because
  // _to_num short-circuits on bool first — String(true) would yield "true"
  // and read as 0 here, the opposite of what runs.
  if (typeof value === 'boolean') return value ? '1' : '0'
  // Arrays and objects make float() raise TypeError. Stringifying first would
  // turn an imported [1] into "1", while the backend sends 0.0.
  if (typeof value === 'object') return fallback
  const text = String(value)
  // Deliberately not Number(): JavaScript also accepts 0x/0o/0b literals and
  // "Infinity", which float() rejects — it would coerce them to 0.0 while the
  // editor kept displaying the original spelling. Both checks are needed: the
  // regex rejects spellings float() cannot parse (0x10, Infinity), isFinite
  // rejects ones it parses into infinity (1e309).
  const trimmed = text.trim()
  return BACKEND_NUMBER_RE.test(trimmed) && Number.isFinite(Number(trimmed)) ? text : fallback
}
