import { trimLikeStrip } from '@/utils/pythonWhitespace'

// The boolean rule the backend applies to Logic values, kept in one place so
// the block card and the configuration panel cannot drift apart.
//
// Mirrors GraphExecutor._to_bool: None is false; a string is false only for
// these spellings (case-insensitive, stripped with str.strip()'s own character
// set — JavaScript's trim() is not the same one, see pythonWhitespace.js); anything else follows Python's
// bool(), where an empty collection is false and a non-empty one — even [0] —
// is true.
export const BACKEND_FALSE_WORDS = new Set(['0', 'false', 'no', 'off', ''])

export function isBackendFalse(value) {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return BACKEND_FALSE_WORDS.has(trimLikeStrip(value).toLowerCase())
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return !value
}

// Python's own truthiness for a raw configured value. Distinct from
// isBackendFalse above: this is the rule for the schema settings whose backend
// consumer is a plain `if d.get(...)` rather than GraphExecutor._to_bool, so
// the string "false" counts as true here. It differs from JavaScript's `!!`
// only for collections — `[]` and `{}` are false in Python and true in
// JavaScript — which is exactly what an imported or API-supplied value can
// carry into a boolean field.
export function isPythonTruthy(value) {
  if (Array.isArray(value)) return value.length > 0
  if (value !== null && typeof value === 'object') return Object.keys(value).length > 0
  return !!value
}
