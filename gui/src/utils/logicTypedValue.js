// One place that turns a configured Logic value into the text the backend
// would actually send, so the configuration panel and the block card cannot
// drift apart. They had drifted four times before this existed: each fix
// landed in one of them and the other kept its own copy of the rule.
//
// Mirrors GraphExecutor._coerce_typed_value. The caller resolves the
// configured value first (an absent key takes the schema default, an explicit
// null does not — see the panel's configuredValue), so every value arriving
// here is one the backend would coerce.
//
// Booleans come back as the literal 'true'/'false' the backend stores; the
// card localizes them for display, the panel uses them as option values.
import { isBackendFalse } from '@/utils/logicBooleans'
import { toBackendNumberText } from '@/utils/logicNumbers'
import { toBackendStringText } from '@/utils/logicStrings'

export function coercedValueText(value, dataType) {
  // Decided with the backend's own rule rather than an exact "true"/"false"
  // match: a supported spelling like "False" or "off" would otherwise read as
  // its opposite, and _to_bool(None) is False.
  if (dataType === 'bool') return isBackendFalse(value) ? 'false' : 'true'
  // The raw value, not a stringified one: an imported native boolean or
  // collection would otherwise show as blank or as JavaScript's own
  // stringification instead of what _to_num and str() send.
  if (dataType === 'number') return toBackendNumberText(value)
  if (dataType === 'string') return toBackendStringText(value)
  // Any other data_type is returned untouched by _coerce_typed_value, so the
  // value may still be a list or object. String() would scalarize those —
  // [1] to "1" and {a:2} to "[object Object]" — so show the JSON form, which
  // is how the value is stored and sent. No str() is applied on this path, so
  // Python's repr would claim a coercion that does not happen.
  if (value === null || value === undefined) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
