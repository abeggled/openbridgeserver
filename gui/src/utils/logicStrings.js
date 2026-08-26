// The string rule the backend applies to Logic values, kept in one place so
// the block card and the configuration panel cannot drift apart — the same
// reason logicBooleans.js and logicNumbers.js exist.
//
// Mirrors GraphExecutor._coerce_typed_value for data_type "string":
// `"" if value is None else str(value)`. A graph is stored as JSON, so an
// imported value can be a native list or object, and Python's str() renders
// those with repr()'d members — "[1]", not JavaScript's "1", and "{'a': 1}",
// not "[object Object]".
//
// Known limit: JSON.parse cannot tell 1.0 from 1, so a float that happens to
// be integral inside a collection renders as "1" where Python prints "1.0".
// The distinction is already lost before this function sees the value.

// Python pads a float exponent to at least two digits: 1e-7 prints as 1e-07.
function numberRepr(value) {
  return String(value).replace(/e([+-])(\d)$/, 'e$10$2')
}

// repr() of a string: single quotes normally, double quotes when the string
// contains a single quote but no double quote, escaping otherwise.
function stringRepr(value) {
  const escaped = value
    .replace(/\\/g, '\\\\')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t')
  if (escaped.includes("'") && !escaped.includes('"')) return `"${escaped}"`
  return `'${escaped.replace(/'/g, "\\'")}'`
}

// Members of a collection are rendered with repr(), not str().
function pythonRepr(value) {
  if (value === null || value === undefined) return 'None'
  if (typeof value === 'boolean') return value ? 'True' : 'False'
  if (typeof value === 'number') return numberRepr(value)
  if (typeof value === 'string') return stringRepr(value)
  if (Array.isArray(value)) return `[${value.map(pythonRepr).join(', ')}]`
  if (typeof value === 'object') {
    const items = Object.entries(value).map(([k, v]) => `${stringRepr(String(k))}: ${pythonRepr(v)}`)
    return `{${items.join(', ')}}`
  }
  return String(value)
}

export function toBackendStringText(value) {
  // str(None) would be "None", but the backend maps a missing value to "".
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? 'True' : 'False'
  if (typeof value === 'number') return numberRepr(value)
  // str() of a collection equals its repr(); a plain string is returned as-is.
  if (typeof value === 'object') return pythonRepr(value)
  return String(value)
}
