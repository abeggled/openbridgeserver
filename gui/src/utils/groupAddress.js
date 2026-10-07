// KNX group addresses in the project's style (#1296).
//
// The API delivers group addresses in the internal three-level notation
// (`1/0/234`) plus the project's `group_address_style`. Everything the Admin
// GUI shows goes through formatGa(), the counterpart of the backend's
// `format_ga()` (obs/adapters/knx/group_address.py). Both are checked against
// the same table, gui/tests/fixtures/ga-format-parity.json, generated from the
// Python side by tools/gen_ga_format_parity.py. Contract:
// docs/architecture/knx-group-addresses.md.

export const THREE_LEVEL = 'ThreeLevel'
export const TWO_LEVEL = 'TwoLevel'
export const FREE = 'Free'
export const GROUP_ADDRESS_STYLES = [THREE_LEVEL, TWO_LEVEL, FREE]
export const DEFAULT_GROUP_ADDRESS_STYLE = THREE_LEVEL

// Upper bound per part, by number of parts in the notation.
const PART_LIMITS = { 3: [31, 7, 255], 2: [31, 2047], 1: [65535] }

// Python's str.strip() set (str.isspace()), so both sides trim the same text.
// eslint-disable-next-line no-control-regex
const PY_WHITESPACE = /^[\t\n\v\f\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\t\n\v\f\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g

function toRaw(value) {
  if (typeof value !== 'string') return null
  const parts = value.replace(PY_WHITESPACE, '').split('/')
  const limits = PART_LIMITS[parts.length]
  if (!limits || !parts.every(part => /^[0-9]+$/.test(part))) return null
  const numbers = parts.map(Number)
  if (numbers.some((number, i) => number > limits[i])) return null
  if (numbers.length === 3) return (numbers[0] << 11) | (numbers[1] << 8) | numbers[2]
  if (numbers.length === 2) return (numbers[0] << 11) | numbers[1]
  return numbers[0]
}

/**
 * Render a group address (any notation) in a project's style.
 *
 * Text that is no group address is returned unchanged: display must not fail on
 * legacy data (the backend's format_ga raises there). An unknown style throws,
 * like format_ga; the style store only ever hands out known styles.
 */
export function formatGa(address, style) {
  if (!GROUP_ADDRESS_STYLES.includes(style)) throw new Error(`Unknown group address style: ${style}`)
  const raw = toRaw(address)
  if (raw === null) return address
  if (style === THREE_LEVEL) return `${raw >> 11}/${(raw >> 8) & 0x7}/${raw & 0xff}`
  if (style === TWO_LEVEL) return `${raw >> 11}/${raw & 0x7ff}`
  return String(raw)
}
