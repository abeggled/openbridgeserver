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
// Known limit — JSON.parse erases Python's int/float distinction, and the two
// print differently ('5' vs '5.0'). The reading is recovered from the JSON
// token the value would be written as, which is exact for every value that
// travelled through the editor (verified over 650 browser round trips) and
// wrong only where the stored JSON spells an INTEGRAL number differently than
// JavaScript would — a plain "5.0", which has no JavaScript literal and reads
// back as int 5, or a large one such as "1e+16" or "1000000000000000000000",
// where the two disagree on digits versus exponent.
//
// Second known limit — JavaScript orders integer-like object keys numerically
// as an intrinsic property of the object, so JSON.parse has already discarded
// the insertion order that Python preserves: {"10":..,"2":..} enumerates as
// 2, 10 here and as 10, 2 there, whichever way it is enumerated. Recovering it
// would mean parsing graph payloads into order-preserving Maps across the API
// layer, which is out of proportion to numeric-looking keys in an edge value.
//
// Third known limit — the non-printable test below uses the JavaScript
// engine's Unicode tables, and the server's Python has its own. They disagree
// about code points assigned between the two versions: U+088F is unassigned
// in Unicode 16 (escaped by Python 3.14) and assigned in Unicode 17 (printed
// literally by a current browser). There is no single backend version to pin
// to — the Python shipped in the Docker image, the LXC template and a local
// install can each differ — and dropping category Cn would break the far more
// common case where both runtimes agree a code point is unassigned.

// Python's float repr switches to scientific notation outside 1e-4 <= |v| <
// 1e16 — a wider fixed range than JavaScript's — pads the exponent to two
// digits, and always keeps a decimal point.
// Only ever reached for a finite, non-integral value, so there is no zero and
// no integral case to special-case: inside the fixed range JavaScript always
// prints such a value with a decimal point already.
function floatRepr(value) {
  const [mantissa, exponent] = value.toExponential().split('e')
  const exp = Number(exponent)
  if (exp < -4 || exp >= 16) {
    const digits = String(Math.abs(exp)).padStart(2, '0')
    return `${mantissa}e${exp < 0 ? '-' : '+'}${digits}`
  }
  return String(value)
}

function numberRepr(value) {
  if (Number.isNaN(value)) return 'nan'
  if (!Number.isFinite(value)) return value > 0 ? 'inf' : '-inf'
  // JSON.parse erases Python's int/float distinction, but the JSON *token*
  // does not: a number written without '.', 'e' or 'E' is an int to Python's
  // parser. Re-serializing recovers the token this value travels as, which is
  // what the backend parsed and stored for anything the editor saved — the
  // browser writes 1e20 as 100000000000000000000 and 1e21 as 1e+21, and
  // Python reads those as int and float respectively.
  // JSON.stringify erases the sign of negative zero, rendering it "0", so the
  // token rule below cannot see it. A JS -0 can only come from a signed token,
  // and Python prints that float as -0.0.
  if (Object.is(value, -0)) return '-0.0'
  const token = JSON.stringify(value)
  return /[.eE]/.test(token) ? floatRepr(value) : token
}

// repr() of a string: single quotes normally, double quotes when the string
// contains a single quote but no double quote, escaping otherwise.
// Everything str.isprintable() rejects: the Unicode categories Cc, Cf, Cs,
// Co, Cn, Zl, Zp and Zs — the ASCII space being the one printable exception.
const NON_PRINTABLE_RE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/gu
const NAMED_ESCAPES = { '\n': '\\n', '\r': '\\r', '\t': '\\t' }

// Python escapes a non-printable character by code point width: \xXX below
// U+0100, \uXXXX below U+10000, \UXXXXXXXX above.
function escapeCodePoint(char) {
  if (char === ' ') return char
  if (NAMED_ESCAPES[char]) return NAMED_ESCAPES[char]
  const code = char.codePointAt(0)
  if (code < 0x100) return `\\x${code.toString(16).padStart(2, '0')}`
  if (code < 0x10000) return `\\u${code.toString(16).padStart(4, '0')}`
  return `\\U${code.toString(16).padStart(8, '0')}`
}

function stringRepr(value) {
  const escaped = value
    .replace(/\\/g, '\\\\')
    .replace(NON_PRINTABLE_RE, escapeCodePoint)
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
