// The whitespace Python skips around a value, which JavaScript's trim() does
// NOT match — in both directions. trim() also strips U+FEFF, which Python does
// not, and it leaves U+0085, which Python does strip. Using trim() therefore
// made the editor display a different value from the one the executor sends.
//
// The two backend paths do not even agree with each other: str.strip(), used
// by GraphExecutor._to_bool, additionally strips U+001C-U+001F, which float()
// — behind _to_num — rejects. Both sets were derived by running CPython and
// the JavaScript engine over every code point up to U+3100 rather than assumed
// from the "whitespace" the two languages document.

// What float() skips: 25 code points.
const FLOAT_SPACE = '\\t\\n\\v\\f\\r \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'
// What str.strip() removes: the same, plus the four ASCII separators.
const STRIP_SPACE = `\\u001c-\\u001f${FLOAT_SPACE}`

const trimmer = set => new RegExp(`^[${set}]+|[${set}]+$`, 'gu')
const FLOAT_TRIM_RE = trimmer(FLOAT_SPACE)
const STRIP_TRIM_RE = trimmer(STRIP_SPACE)

// Use for a value that reaches the backend through float() (_to_num).
export function trimLikeFloat(text) {
  return text.replace(FLOAT_TRIM_RE, '')
}

// Use for a value that reaches the backend through str.strip() (_to_bool).
export function trimLikeStrip(text) {
  return text.replace(STRIP_TRIM_RE, '')
}
