/**
 * Applies a math formula to a numeric value.
 * Variable: x (the current value).
 * Returns the original value on any error.
 */
export function applyFormula(formula: string, value: number): number {
  if (!formula.trim()) return value
  try {
    // eslint-disable-next-line no-new-func
    const fn = new Function('x', `'use strict'; return (${formula})`)
    const result = fn(value)
    return typeof result === 'number' && isFinite(result) ? result : value
  } catch {
    return value
  }
}

/**
 * Applies a value map (enum substitution) to any value.
 * Booleans are normalized to lowercase ("true" / "false"). Exact keys win,
 * then string keys are matched case-insensitively. Boolean values also fall
 * back to numeric keys ("1" / "0").
 * Returns the original value if no match is found.
 */
export function applyValueMap(
  valueMap: Record<string, string>,
  value: unknown,
): unknown {
  if (!valueMap || Object.keys(valueMap).length === 0) return value
  const keys = typeof value === 'boolean'
    ? [String(value), value ? '1' : '0']
    : [String(value)]

  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(valueMap, key)) return valueMap[key]

    const foldedKey = key.toLowerCase()
    const fallbackKey = Object.keys(valueMap).find(
      mapKey => mapKey.toLowerCase() === foldedKey,
    )
    if (fallbackKey !== undefined) return valueMap[fallbackKey]
  }

  return value
}
