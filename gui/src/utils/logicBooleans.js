// The boolean rule the backend applies to Logic values, kept in one place so
// the block card and the configuration panel cannot drift apart.
//
// Mirrors GraphExecutor._to_bool: a string is false only for these spellings
// (case-insensitive, trimmed), null/undefined is false, everything else true.
export const BACKEND_FALSE_WORDS = new Set(['0', 'false', 'no', 'off', ''])

export function isBackendFalse(value) {
  if (value === null || value === undefined) return true
  return BACKEND_FALSE_WORDS.has(String(value).trim().toLowerCase())
}
